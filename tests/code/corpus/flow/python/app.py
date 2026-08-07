"""Flow corpus (Python). Top-level functions with a known call graph.

Ground truth call edges are recorded in ../ground_truth.json. Every callee has a
single definition so name-based resolution is unambiguous.
"""


def handle_request():
    validate_input()
    result = compute()
    write_response(result)


def validate_input():
    check_schema()
    check_limits()


def check_schema():
    normalize()


def check_limits():
    normalize()


def normalize():
    return 1


def compute():
    total = aggregate()
    return finalize(total)


def aggregate():
    load_rows()
    return reduce_rows()


def load_rows():
    normalize()


def reduce_rows():
    return 2


def finalize(total):
    write_log()
    return total


def write_response(result):
    write_log()


def write_log():
    return None


def unrelated_helper():
    return 42
