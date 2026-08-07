"""Notebooks, single-file components, and infrastructure-as-code parsing.

These formats are not one grammar over one file: a notebook is JSON holding
cells, a component wraps a script block in markup, and the infrastructure
formats carry their own structure. Each is unwrapped and then read, and each is
checked here for the things that make it useful downstream: real symbols, real
edges, and honest behaviour when the file is not what its extension suggests.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dkg.code import iac, notebooks, sfc
from dkg.code.capability import grammar_available
from dkg.code.parser import is_parsable, language_for, parse_source
from dkg.core.errors import UnsupportedFormatError

CORPUS = Path(__file__).resolve().parents[2] / "tests" / "code" / "corpus" / "langs"


def _needs(language: str):
    return pytest.mark.skipif(
        not grammar_available(language), reason=f"the {language} grammar is not installed"
    )


def _symbols(path):
    parsed = parse_source(path)
    return {(s.kind, s.name) for s in parsed.symbols if s.kind != "module"}


def _refs(path):
    parsed = parse_source(path)
    return {(r.kind, r.name) for r in parsed.references}


# -- notebooks -----------------------------------------------------------------


@_needs("python")
def test_jupyter_code_cells_become_ordinary_symbols():
    got = _symbols(CORPUS / "jupyter" / "analysis.ipynb")
    assert ("function", "circle_area") in got
    assert ("class", "Report") in got
    assert ("method", "render") in got


@_needs("python")
def test_jupyter_markdown_cells_contribute_no_symbols():
    book = notebooks.read_jupyter(CORPUS / "jupyter" / "analysis.ipynb")
    assert book.format == "jupyter"
    assert book.language == "python"
    assert "# Analysis" not in book.source


@_needs("python")
def test_jupyter_cell_boundaries_are_reported_so_lines_can_be_mapped_back():
    book = notebooks.read_jupyter(CORPUS / "jupyter" / "analysis.ipynb")
    assert len(book.cell_lines) == 3
    first_start, first_end, first_index = book.cell_lines[0]
    assert first_start == 1 and first_end >= first_start
    assert notebooks.cell_for_line(book, first_start) == first_index
    assert notebooks.cell_for_line(book, 10_000) is None


def test_jupyter_rejects_a_kernel_language_with_no_parser():
    doc = {
        "cells": [{"cell_type": "code", "source": ["x = 1\n"]}],
        "metadata": {"kernelspec": {"language": "brainfuck"}},
    }
    with pytest.raises(UnsupportedFormatError):
        notebooks.read_jupyter("x.ipynb", json.dumps(doc))


def test_jupyter_rejects_malformed_json_rather_than_returning_nothing():
    from dkg.core.errors import IngestError

    with pytest.raises(IngestError):
        notebooks.read_jupyter("x.ipynb", "{not json")


@_needs("python")
def test_databricks_notebook_is_detected_by_marker_not_extension():
    path = CORPUS / "databricks" / "etl.py"
    assert notebooks.is_databricks_notebook(path)
    assert not notebooks.is_databricks_notebook(CORPUS / "python" / "shapes.py")
    got = _symbols(path)
    assert ("function", "clean") in got
    assert ("class", "Pipeline") in got


@_needs("python")
def test_databricks_magic_cells_in_another_language_are_skipped_and_counted():
    book = notebooks.read_databricks(CORPUS / "databricks" / "etl.py")
    assert book.skipped_cells == 1
    assert "SELECT 1" not in book.source


# -- single-file components ----------------------------------------------------


@_needs("typescript")
def test_vue_script_block_is_parsed_as_typescript():
    got = _symbols(CORPUS / "vue" / "Counter.vue")
    assert ("function", "bump") in got
    assert ("type", "Props") in got
    assert ("calls", "increment") in _refs(CORPUS / "vue" / "Counter.vue")


@_needs("typescript")
def test_svelte_module_and_instance_blocks_are_both_read():
    got = _symbols(CORPUS / "svelte" / "Panel.svelte")
    assert ("function", "panelClass") in got
    assert ("function", "apply") in got
    component = sfc.read_component(CORPUS / "svelte" / "Panel.svelte")
    assert {b.kind for b in component.blocks} == {"module", "script"}


@_needs("typescript")
def test_astro_frontmatter_is_read_as_typescript():
    got = _symbols(CORPUS / "astro" / "Page.astro")
    assert ("function", "heading") in got
    component = sfc.read_component(CORPUS / "astro" / "Page.astro")
    assert component.blocks[0].kind == "frontmatter"


def test_component_with_only_markup_yields_no_symbols_and_is_not_an_error():
    parsed = parse_source("Empty.vue", "<template><div>hi</div></template>\n")
    assert [s.kind for s in parsed.symbols] == ["module"]


def test_component_script_in_an_unparsable_language_is_counted_not_guessed():
    component = sfc.read_component(
        "Odd.vue", '<script lang="coffee">x = 1</script>\n'
    )
    assert component.skipped_blocks == 1
    assert component.blocks == []


@_needs("typescript")
def test_component_line_offsets_point_back_into_the_component_file():
    component = sfc.read_component(CORPUS / "vue" / "Counter.vue")
    assert component.blocks
    assert component.blocks[0].line_offset > 1


# -- Terraform and HCL ---------------------------------------------------------


@_needs("hcl")
def test_terraform_blocks_become_symbols_under_their_terraform_address():
    got = _symbols(CORPUS / "hcl" / "main.tf")
    assert ("type", "aws_s3_bucket.data") in got
    assert ("type", "var.bucket_name") in got
    assert ("type", "data.aws_caller_identity.current") in got
    assert ("class", "module.network") in got
    assert ("type", "output.bucket_arn") in got


@_needs("hcl")
def test_terraform_addresses_referenced_from_another_block_become_edges():
    refs = _refs(CORPUS / "hcl" / "main.tf")
    assert ("calls", "aws_s3_bucket.data") in refs
    assert ("calls", "var.bucket_name") in refs
    # A module source is an import of the module it pulls in.
    assert ("imports", "network") in refs


@_needs("hcl")
def test_terraform_address_scheme_matches_how_terraform_writes_it():
    assert iac._block_address("resource", ["aws_s3_bucket", "data"]) == "aws_s3_bucket.data"
    assert iac._block_address("data", ["aws_ami", "base"]) == "data.aws_ami.base"
    assert iac._block_address("variable", ["cidr"]) == "var.cidr"
    assert iac._block_address("module", ["vpc"]) == "module.vpc"
    assert iac._block_address("terraform", []) == "terraform"


@_needs("hcl")
def test_generic_hcl_outside_terraform_still_parses():
    got = _symbols(CORPUS / "hcl" / "nomad.hcl")
    assert ("type", "job.web") in got


# -- Ansible -------------------------------------------------------------------


@_needs("yaml")
def test_ansible_plays_and_tasks_become_symbols():
    got = _symbols(CORPUS / "ansible" / "site.yml")
    assert ("class", "Configure_web_servers") in got
    assert ("method", "Install_nginx") in got
    assert ("class", "Configure_database_servers") in got


@_needs("yaml")
def test_ansible_module_invocations_and_includes_become_edges():
    refs = _refs(CORPUS / "ansible" / "site.yml")
    assert ("calls", "package") in refs
    assert ("calls", "service") in refs
    assert ("imports", "common") in refs
    assert ("imports", "nginx") in refs
    assert ("imports", "extras.yml") in refs


@_needs("yaml")
def test_a_role_task_file_without_a_play_still_parses():
    got = _symbols(CORPUS / "ansible" / "tasks.yml")
    assert ("function", "Create_directory") in got


@_needs("yaml")
def test_plain_yaml_configuration_is_not_claimed_as_ansible():
    config = "version: 2\njobs:\n  build:\n    steps:\n      - checkout\n"
    assert language_for("config.yml") == "ansible"
    # The extension resolves, but the content decides, and ingestion asks first.
    assert not iac.looks_like_ansible("config.yml", config)
    assert not is_parsable("config.yml", config)


@_needs("yaml")
def test_an_ansible_playbook_is_recognised_by_content():
    playbook = "- name: Play\n  hosts: all\n  tasks:\n    - name: Ping\n      ping:\n"
    assert iac.looks_like_ansible("anything.yml", playbook)
    assert is_parsable("anything.yml", playbook)
