"""AST tabanlı statik kod analizi: fonksiyon sınırları, imza, tip ipuçları."""
import ast
import os
from dataclasses import dataclass, field
from typing import Any, List, Optional


@dataclass
class Param:
    name: str
    annotation: str = ""

    @property
    def kind(self) -> str:
        """Tip ipucundan kaba bir kategori çıkarır."""
        a = self.annotation.lower().replace("typing.", "")
        if a.startswith("optional["):
            a = a[len("optional["):]
        for key, kinds in (
            ("seq", ("list", "sequence", "tuple", "iterable", "collection")),
            ("map", ("dict", "mapping")),
            ("str", ("str",)),
            ("bool", ("bool",)),
            ("int", ("int",)),
            ("float", ("float", "number", "decimal")),
        ):
            if any(a.startswith(k) for k in kinds):
                return key
        return "any"


@dataclass
class FunctionInfo:
    name: str
    lineno: int
    end_lineno: int
    params: List[Param] = field(default_factory=list)
    returns: str = ""
    body_start: int = 0          # docstring sonrası ilk ifadenin satırı
    body_indent: str = "    "
    is_method: bool = False

    def contains(self, line: int) -> bool:
        return self.lineno <= line <= self.end_lineno


def resolve_repo_file(repo_path: str, log_path: str) -> Optional[str]:
    """Log'daki (CI makinesine ait) yolu, yerel repo içindeki göreli yola eşler."""
    norm = log_path.replace("\\", "/")
    if os.path.isabs(norm) and os.path.exists(norm) and os.path.commonpath([os.path.abspath(repo_path), norm]) == os.path.abspath(repo_path):
        return os.path.relpath(norm, repo_path)
    parts = [p for p in norm.split("/") if p]
    for i in range(len(parts)):
        candidate = os.path.join(*parts[i:])
        if os.path.isfile(os.path.join(repo_path, candidate)):
            return candidate.replace(os.sep, "/")
    return None


def module_name(rel_path: str) -> str:
    return rel_path[:-3].replace("/", ".").replace("\\", ".") if rel_path.endswith(".py") else rel_path


def list_functions(source: str) -> List[FunctionInfo]:
    tree = ast.parse(source)
    out: List[FunctionInfo] = []
    lines = source.splitlines()

    def visit(node: ast.AST, in_class: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                args = child.args.posonlyargs + child.args.args + child.args.kwonlyargs
                params = [Param(a.arg, ast.unparse(a.annotation) if a.annotation else "") for a in args]
                if in_class and params and params[0].name in ("self", "cls"):
                    params = params[1:]
                body = child.body
                first = body[0]
                if (isinstance(first, ast.Expr) and isinstance(getattr(first, "value", None), ast.Constant)
                        and isinstance(first.value.value, str) and len(body) > 1):
                    first = body[1]
                indent_line = lines[first.lineno - 1]
                out.append(FunctionInfo(
                    name=child.name, lineno=child.lineno, end_lineno=child.end_lineno or child.lineno,
                    params=params, returns=ast.unparse(child.returns) if child.returns else "",
                    body_start=first.lineno, body_indent=indent_line[: len(indent_line) - len(indent_line.lstrip())],
                    is_method=in_class,
                ))
                visit(child, False)
            elif isinstance(child, ast.ClassDef):
                visit(child, True)
            else:
                visit(child, in_class)

    visit(tree, False)
    return out


def find_function(source: str, name: Optional[str] = None, line: Optional[int] = None) -> Optional[FunctionInfo]:
    funcs = list_functions(source)
    if line is not None:
        hits = [f for f in funcs if f.contains(line)]
        if hits:
            return min(hits, key=lambda f: f.end_lineno - f.lineno)  # en içteki
    if name:
        for f in funcs:
            if f.name == name:
                return f
    return None


def default_return_literal(annotation: str) -> str:
    """Dönüş tipi ipucuna göre 'boş/nötr' değerin kaynak kod gösterimi."""
    a = annotation.replace("typing.", "").strip()
    low = a.lower()
    if not a or low.startswith("optional[") or low in ("none", "any") or "| none" in low:
        return "None"
    table = (
        (("list", "sequence", "iterable", "collection"), "[]"),
        (("tuple",), "()"),
        (("dict", "mapping"), "{}"),
        (("set", "frozenset"), "set()"),
        (("str",), '""'),
        (("bool",), "False"),
        (("int",), "0"),
        (("float",), "0.0"),
    )
    for keys, lit in table:
        if any(low.startswith(k) for k in keys):
            return lit
    return "None"


def is_simple_literal(value: Any) -> bool:
    try:
        return ast.literal_eval(repr(value)) == value
    except Exception:
        return False
