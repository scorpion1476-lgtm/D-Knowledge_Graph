"""Framework-aware PHP parsing: autoload, routes, models, and Blade views.

The point of these edges is that a structural parse alone cannot produce them:
a controller is reached through a route table, a view through a dotted name, and
a class through an autoload rule. Each test below checks that the edge exists
and points at something real, and that a project using no framework is
unaffected by any of it.
"""

from __future__ import annotations

import json

import pytest

from dkg.code import frameworks
from dkg.code.capability import grammar_available
from dkg.code.parser import parse_source

needs_php = pytest.mark.skipif(
    not grammar_available("php"), reason="the php grammar is not installed"
)


# -- Composer PSR-4 -----------------------------------------------------------


def test_psr4_resolves_a_class_name_to_the_file_that_defines_it(tmp_path):
    (tmp_path / "composer.json").write_text(
        json.dumps({"autoload": {"psr-4": {"App\\": "app/", "App\\Models\\": "src/models/"}}}),
        encoding="utf-8",
    )
    autoload = frameworks.load_composer_autoload(tmp_path)
    assert autoload.resolve("App\\Http\\Controllers\\UserController") == "app/Http/Controllers/UserController.php"
    # The longest matching prefix wins, which is Composer's own rule.
    assert autoload.resolve("App\\Models\\Post") == "src/models/Post.php"


def test_psr4_reports_no_path_for_a_class_no_rule_covers(tmp_path):
    (tmp_path / "composer.json").write_text(
        json.dumps({"autoload": {"psr-4": {"App\\": "app/"}}}), encoding="utf-8"
    )
    autoload = frameworks.load_composer_autoload(tmp_path)
    # A vendor class is not guessed at.
    assert autoload.resolve("Vendor\\Package\\Thing") is None


def test_psr0_underscores_become_directories(tmp_path):
    (tmp_path / "composer.json").write_text(
        json.dumps({"autoload": {"psr-0": {"Legacy_": "lib/"}}}), encoding="utf-8"
    )
    autoload = frameworks.load_composer_autoload(tmp_path)
    assert autoload.resolve("Legacy_Old_Thing") == "lib/Legacy/Old/Thing.php"


def test_a_missing_or_malformed_composer_file_is_not_an_error(tmp_path):
    assert frameworks.load_composer_autoload(tmp_path).prefixes == []
    (tmp_path / "composer.json").write_text("{not json", encoding="utf-8")
    assert frameworks.load_composer_autoload(tmp_path).prefixes == []


@needs_php
def test_autoload_turns_a_use_statement_into_a_file_level_import(tmp_path):
    (tmp_path / "composer.json").write_text(
        json.dumps({"autoload": {"psr-4": {"App\\": "app/"}}}), encoding="utf-8"
    )
    autoload = frameworks.load_composer_autoload(tmp_path)
    text = "<?php\nnamespace App;\nuse App\\Models\\Post;\nuse Vendor\\Thing;\n"
    parsed = parse_source("app/Service.php", text)
    frameworks.apply_autoload(parsed, text, autoload)
    imported = {r.name for r in parsed.references if r.kind == "imports"}
    assert "Post" in imported
    # The vendor class resolves to nothing, so no false file-level edge appears.
    assert frameworks.load_composer_autoload(tmp_path).resolve("Vendor\\Thing") is None


# -- routes --------------------------------------------------------------------


@needs_php
def test_route_definitions_become_symbols_and_point_at_their_action():
    text = (
        "<?php\n"
        "Route::get('/users', [UserController::class, 'index']);\n"
        "Route::post('/users', 'UserController@store');\n"
        "Route::get('/dash', DashboardController::class);\n"
    )
    parsed = parse_source("routes/web.php", text)
    names = {s.name for s in parsed.symbols}
    assert "GET /users" in names
    assert "POST /users" in names
    # routes_to, not calls: the framework dispatches from a URL at runtime, and
    # flattening that into "calls" would make "what serves this endpoint"
    # unanswerable without guessing which calls are really routes.
    routed = {r.name for r in parsed.references if r.kind == "routes_to"}
    assert {"index", "store"} <= routed
    # A single-action controller is dispatched through __invoke.
    assert "__invoke" in routed
    assert {"UserController", "DashboardController"} <= routed
    # The distinction is the point, so check it survives.
    assert not {r.name for r in parsed.references if r.kind == "calls"} & routed


@needs_php
def test_the_route_edge_starts_at_the_route_not_at_the_file():
    text = "<?php\nRoute::get('/users', [UserController::class, 'index']);\n"
    parsed = parse_source("routes/web.php", text)
    route = next(s for s in parsed.symbols if s.name == "GET /users")
    sources = {r.from_qualified for r in parsed.references if r.name == "index"}
    assert route.qualified in sources


# -- models --------------------------------------------------------------------


@needs_php
def test_a_class_extending_the_framework_base_is_recorded_as_a_model():
    text = (
        "<?php\n"
        "namespace App\\Models;\n"
        "use Illuminate\\Database\\Eloquent\\Model;\n"
        "class Post extends Model {\n"
        "    public function comments() { return $this->hasMany(Comment::class); }\n"
        "    public function author() { return $this->belongsTo(User::class); }\n"
        "}\n"
    )
    parsed = parse_source("app/Models/Post.php", text)
    assert any(s.name == "model:Post" for s in parsed.symbols)
    # relates_to, not calls: an association declares a link between models
    # rather than invoking anything.
    related = {r.name for r in parsed.references if r.kind == "relates_to"}
    assert {"Comment", "User"} <= related
    assert not {"Comment", "User"} & {r.name for r in parsed.references if r.kind == "calls"}
    assert ("inherits", "Model") in {(r.kind, r.name) for r in parsed.references}


@needs_php
def test_a_plain_class_is_not_recorded_as_a_model():
    text = "<?php\nclass Helper extends BaseHelper {\n    public function run() {}\n}\n"
    parsed = parse_source("app/Helper.php", text)
    assert not any(s.name.startswith("model:") for s in parsed.symbols)


# -- Blade views ---------------------------------------------------------------


@needs_php
def test_a_view_call_becomes_an_edge_from_the_method_that_renders():
    text = (
        "<?php\n"
        "class UserController {\n"
        "    public function index() { return view('admin.users.index'); }\n"
        "}\n"
    )
    parsed = parse_source("app/Http/Controllers/UserController.php", text)
    view_edges = [r for r in parsed.references if r.name == "view:admin.users.index"]
    assert view_edges
    assert view_edges[0].from_qualified.endswith("UserController.index")


def test_a_blade_template_is_a_node_and_names_the_templates_it_pulls_in():
    text = "@extends('layouts.app')\n@include('partials.header')\n<h1>{{ $t }}</h1>\n"
    parsed = parse_source("resources/views/admin/users/index.blade.php", text)
    assert parsed.language == "blade"
    assert any(s.name == "view:admin.users.index" for s in parsed.symbols)
    # renders, not imports: a template is rendered, and calling that an import
    # loses the difference between a dependency and a presentation choice.
    pulled = {r.name for r in parsed.references if r.kind == "renders"}
    assert {"view:layouts.app", "view:partials.header"} <= pulled
    assert not pulled & {r.name for r in parsed.references if r.kind == "imports"}


def test_the_view_name_a_template_answers_to_matches_the_name_code_calls_it_by():
    path = "resources/views/admin/users/index.blade.php"
    assert frameworks.blade_view_name(path) == "admin.users.index"
    assert path in frameworks.view_path("admin.users.index")


def test_a_blade_template_is_not_parsed_as_ordinary_php():
    # The extension ends in .php, so without the Blade check the template markup
    # would be handed to the PHP grammar and produce nothing useful.
    assert frameworks.is_blade_template("resources/views/x.blade.php")
    assert not frameworks.is_blade_template("app/Models/Post.php")


# -- no framework, no change ---------------------------------------------------


@needs_php
def test_plain_php_gains_no_framework_symbols():
    text = "<?php\nfunction add($a, $b) { return $a + $b; }\nclass Calc { public function run() { return add(1, 2); } }\n"
    parsed = parse_source("lib/calc.php", text)
    assert not any(s.name.startswith(("route:", "model:", "view:")) for s in parsed.symbols)
    assert {s.name for s in parsed.symbols if s.kind != "module"} == {"add", "Calc", "run"}
