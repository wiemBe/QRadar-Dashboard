import ast, pathlib, re, json

root = pathlib.Path("/home/efe/Documents/QRadar-Dash/qradar-mcp/tools")
rows = []

def const_str(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None

for py in sorted(root.rglob("*.py")):
    if py.name in ("__init__.py", "base.py", "schema.py", "fastmcp_adapter.py"):
        continue
    src = py.read_text()
    tree = ast.parse(src)
    for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
        name = verb = approval = None
        for fn in [n for n in cls.body if isinstance(n, ast.FunctionDef)]:
            if fn.name in ("name", "http_verb", "approval_required"):
                for r in ast.walk(fn):
                    if isinstance(r, ast.Return) and r.value is not None:
                        v = const_str(r.value)
                        if v is None and isinstance(r.value, ast.Constant):
                            v = r.value.value
                        if fn.name == "name" and isinstance(v, str):
                            name = v
                        elif fn.name == "http_verb" and isinstance(v, str):
                            verb = v
                        elif fn.name == "approval_required":
                            approval = v
        if not name:
            continue
        # endpoint: find self.client.<verb>( "..." ) or f-strings
        eps = []
        for call in [n for n in ast.walk(tree) if isinstance(n, ast.Call)]:
            f = call.func
            if isinstance(f, ast.Attribute) and f.attr in ("get", "post", "delete", "put", "patch"):
                if isinstance(f.value, ast.Attribute) and f.value.attr == "client":
                    if call.args:
                        a = call.args[0]
                        if isinstance(a, ast.Constant):
                            eps.append((f.attr.upper(), a.value))
                        elif isinstance(a, ast.JoinedStr):
                            parts = []
                            for v in a.values:
                                if isinstance(v, ast.Constant):
                                    parts.append(str(v.value))
                                else:
                                    inner = v.value
                                    nm = getattr(inner, "id", None) or getattr(inner, "attr", None) or "param"
                                    parts.append("{%s}" % nm)
                            eps.append((f.attr.upper(), "".join(parts)))
        rows.append({
            "group": py.parent.name,
            "tool": name,
            "verb": verb,
            "approval_required": approval,
            "endpoints": eps,
        })

print(json.dumps(rows, indent=1))
print("TOTAL", len(rows))
