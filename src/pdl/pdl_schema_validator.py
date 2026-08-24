# pylint: disable=import-outside-toplevel
from typing import Any

from .pdl_ast import FunctionBlock, PdlTypeType
from .pdl_location_utils import located_message
from .pdl_schema_error_analyzer import analyze_errors
from .pdl_schema_utils import get_json_schema, pdltype_to_jsonschema


def type_check_args(
    args: dict[str, Any] | None,
    params: dict[str, PdlTypeType] | None,
    loc,
) -> list[str]:
    if (args == {} or args is None) and (params is None or params == {}):
        return []
    if args is None:
        args_copy = {}
    else:
        args_copy = args.copy()
    if params is None:
        params_copy = {}
    else:
        params_copy = params.copy()
    # if "pdl_context" not in args_copy:
    #     args_copy["pdl_context"] = "pdl_context"
    # if "pdl_context" not in params_copy:
    if "pdl_context" in args_copy:
        # params_copy["pdl_context"] = [{"role": "str?", "content": "str"}]
        params_copy["pdl_context"] = ["object"]
    for k, v in args_copy.items():
        if isinstance(v, FunctionBlock):
            args_copy[k] = v.model_dump()
    schema = get_json_schema(params_copy, False)
    if schema is None:
        return ["Error obtaining a valid schema from function parameters definition"]
    return type_check(args_copy, schema, loc, subject="the arguments")


def type_check_spec(result: Any, spec: PdlTypeType, loc) -> list[str]:
    schema = pdltype_to_jsonschema(spec, False)
    if schema is None:
        return ["Error obtaining a valid schema from spec"]
    return type_check(result, schema, loc, subject="the block's result")


def type_check(
    result: Any, schema: dict[str, Any], loc, subject: str = ""
) -> list[str]:
    """Validate a value the *program produced* against a type it declared.

    `subject` is what makes the difference visible to `analyze_errors`. The
    location here points at the `spec:` or the `args:` that declared the type,
    while `result` is a value computed at run time, so a message that named the
    field the location sits on would blame the declaration for the value: "the
    block's result should be a list", not "`spec:` should be a list".
    """
    from jsonschema import ValidationError, validate

    try:
        validate(instance=result, schema=schema)
    except ValidationError as e:
        errors = analyze_errors({}, schema, result, loc, subject=subject)
        if len(errors) == 0:
            errors = [located_message(loc, e.message)]
        return errors
    return []
