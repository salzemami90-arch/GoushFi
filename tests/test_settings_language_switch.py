import ast
from pathlib import Path


SETTINGS_PAGE = Path(__file__).resolve().parents[1] / "pages_floosy" / "settings_page.py"
APP_PAGE = Path(__file__).resolve().parents[1] / "app.py"


def _session_state_key(target: ast.expr) -> str:
    if not isinstance(target, ast.Subscript):
        return ""
    owner = target.value
    if not (
        isinstance(owner, ast.Attribute)
        and owner.attr == "session_state"
        and isinstance(owner.value, ast.Name)
        and owner.value.id == "st"
    ):
        return ""
    if isinstance(target.slice, ast.Constant) and isinstance(target.slice.value, str):
        return target.slice.value
    return ""


def test_settings_render_does_not_mutate_sidebar_widget_state():
    tree = ast.parse(SETTINGS_PAGE.read_text(encoding="utf-8"))
    render = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "render"
    )

    forbidden_lines = []
    for node in ast.walk(render):
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        elif isinstance(node, ast.AugAssign):
            targets = [node.target]
        if any(_session_state_key(target) == "sidebar_section" for target in targets):
            forbidden_lines.append(node.lineno)

    assert forbidden_lines == [], (
        "settings_page.render() runs after the sidebar radio is instantiated; "
        f"mutating its widget key raises StreamlitAPIException at lines {forbidden_lines}"
    )


def test_language_switch_queues_settings_navigation():
    source = SETTINGS_PAGE.read_text(encoding="utf-8")

    assert 'st.session_state["_pending_sidebar_section"] = "settings"' in source


def test_pending_sidebar_selection_is_consumed_before_radio_instantiation():
    source = APP_PAGE.read_text(encoding="utf-8")

    pending_line = source.index('pop("_pending_sidebar_section"')
    radio_line = source.index("st.sidebar.radio(")

    assert pending_line < radio_line
