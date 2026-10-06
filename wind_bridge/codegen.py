"""Translate a single Wind code-generator query into MCP parameters, without executing it."""
import ast
import hashlib

from .common import Problem, envelope
from .requests import METHODS, validate


def parse_query(code):
    if not isinstance(code, str) or not 1 <= len(code) <= 20000:
        raise Problem("INVALID_QUERY_CODE", "Paste one bounded Wind code-generator query line")
    try:
        tree = ast.parse(code.strip(), mode="exec")
    except (SyntaxError, RecursionError):
        raise Problem("INVALID_QUERY_CODE", "Cannot parse the query line") from None
    if len(tree.body) != 1 or len(list(ast.walk(tree))) > 300:
        raise Problem("INVALID_QUERY_CODE", "Only one literal Wind query is accepted; do not include imports, login or other statements")
    statement = tree.body[0]
    if isinstance(statement, ast.Assign) and len(statement.targets) == 1 and isinstance(statement.targets[0], ast.Name):
        call = statement.value
    elif isinstance(statement, ast.Expr):
        call = statement.value
    else:
        raise Problem("INVALID_QUERY_CODE", "Expected w.method(...) or result = w.method(...)")
    if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Attribute) or not isinstance(call.func.value, ast.Name) or call.func.value.id != "w":
        raise Problem("INVALID_QUERY_CODE", "Only direct w.method(...) queries are accepted")
    method = call.func.attr
    if method not in METHODS:
        raise Problem("METHOD_NOT_ALLOWED", "Only allowlisted Wind read methods are supported", allowed=list(METHODS))
    required, optional = METHODS[method]
    names = required + optional
    if len(call.args) > len(names):
        raise Problem("INVALID_QUERY_CODE", "Too many positional arguments")
    arguments = {}
    try:
        for name, value in zip(names, call.args):
            arguments[name] = ast.literal_eval(value)
        for keyword in call.keywords:
            if keyword.arg is None or keyword.arg in arguments:
                raise Problem("INVALID_QUERY_CODE", "Keyword expansion and duplicate arguments are not supported")
            arguments[keyword.arg] = ast.literal_eval(keyword.value)
    except (ValueError, TypeError, RecursionError):
        raise Problem("INVALID_QUERY_CODE", "Arguments must be literals; variable references and function calls are not executed") from None
    arguments = validate(method, arguments)
    digest = hashlib.sha256(code.encode()).hexdigest()
    return envelope(method=method, arguments=arguments, executed=False, syntax_valid=True,
                    field_semantics_verified=False, entitlement_verified=False,
                    evidence=f"Provided Wind code-generator query; SHA256={digest}; parsed locally without execution",
                    note="Review field meanings and options in Wind CG before query_wind_data; a parsed query is not proof of valid fields or permissions.")
