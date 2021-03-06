#!/usr/bin/env python3

import ast
import json
import re
import pathlib
import astunparse
import os


def normalize_parameter_name(name):
    # the in-query . parameters are not valid Python variable names.
    # We replace the . with a _ to avoid problem,
    return name.replace(".", "_")


def normalize_description(string_list):
    def _transform(my_list):
        for i in my_list:
            if not i:
                continue
            i = i.replace(" {@term enumerated type}", "")
            i = re.sub(r"{@name DayOfWeek}", "day of the week", i)
            yield i

    if not isinstance(string_list, list):
        raise TypeError

    with_no_line_break = []
    for l in string_list:
        if "\n" in l:
            with_no_line_break += l.split("\n")
        else:
            with_no_line_break.append(l)

    return list(_transform(with_no_line_break))


def python_type(value):
    TYPE_MAPPING = {
        "array": "list",
        "boolean": "bool",
        "integer": "int",
        "object": "dict",
        "string": "str",
    }
    return TYPE_MAPPING.get(value, value)


def gen_documentation(name, description, parameters):

    documentation = {
        "author": [],
        "description": description,
        "module": name,
        "notes": [],
        "options": {},
        "requirements": ["python >= 3.6"],
        "short_description": description,
        "version_added": "1.0.0",
    }

    for parameter in parameters:
        description = []
        option = {}
        option["type"] = parameter.get("type", "string")
        if parameter.get("required"):
            option["required"] = True
        if parameter.get("description"):
            description.append(parameter["description"])
        if parameter.get("subkeys"):
            description.append("Validate attributes are:")
            for subkey in parameter.get("subkeys"):
                subkey["type"] = python_type(subkey["type"])
                description.append(
                    " - C({name}) ({type}): {description}".format(**subkey)
                )
        if "operationIds" in parameter:
            description.append(
                "Used by I(state={})".format(sorted(set(parameter["operationIds"])))
            )
        option["description"] = list(normalize_description(description))
        option["type"] = python_type(option["type"])
        if "enum" in parameter:
            option["choices"] = sorted(parameter["enum"])

        documentation["options"][parameter["name"]] = option
    return documentation


def format_documentation(documentation):
    import yaml

    def _sanitize(input):
        if isinstance(input, str):
            return input.replace("':'", ":")
        elif isinstance(input, list):
            return [l.replace("':'", ":") for l in input]
        elif isinstance(input, dict):
            return {k: _sanitize(v) for k, v in input.items()}
        elif isinstance(input, bool):
            return input
        else:
            raise TypeError

    keys = [
        "module",
        "short_description",
        "description",
        "options",
        "author",
        "version_added",
        "requirements",
    ]
    final = "DOCUMENTATION = '''\n"
    for i in keys:
        final += yaml.dump({i: _sanitize(documentation[i])}, indent=2)
    final += "'''"
    return final


def path_to_name(path):
    _path = path.split("?")[0]

    elements = [i for i in _path.split("/") if i != ""]

    for idx, element in enumerate(elements):
        if "{" in element:
            elements[idx] = (
                f"by_{element}".replace("{", "")
                .replace("}", "")
                .replace("=", "_eq_")
                .replace(":", "_")
                .lower()
            )

    if elements[:1] == ["data"]:
        elements = elements[1:]

    module_name = "_".join(elements).replace("-", "_").replace(":", "_").lower()
    return module_name


def gen_arguments_py(parameters, list_index=None):
    def _add_key(assign, key, value):
        k = [ast.Constant(value=key, kind=None)]
        v = [ast.Constant(value=value, kind=None)]
        assign.value.keys.append(k)
        assign.value.values.append(v)

    ARGUMENT_TPL = """argument_spec['{name}'] = {{}}"""

    for parameter in parameters:
        name = normalize_parameter_name(parameter["name"])
        assign = ast.parse(ARGUMENT_TPL.format(name=name)).body[0]

        if name in ["user_name", "username", "password"]:
            _add_key(assign, "nolog", True)

        if parameter.get("required"):
            if list_index == name:
                pass
            else:
                _add_key(assign, "required", True)

        _add_key(assign, "type", python_type(parameter.get("type", "string")))
        if "enum" in parameter:
            _add_key(assign, "choices", sorted(parameter["enum"]))

        if "operationIds" in parameter:
            _add_key(assign, "operationIds", sorted(parameter["operationIds"]))

        yield assign


class Resource:
    def __init__(self, name):
        self.name = name
        self.operations = {}


class AnsibleModuleBase:
    def __init__(self, resource, definitions):
        self.resource = resource
        self.definitions = definitions
        self.name = resource.name
        self.description = "Handle resource of type {name}".format(name=resource.name)
        self.default_operationIds = None

    def list_index(self):
        for i in ["get", "update", "delete"]:
            if i not in self.resource.operations:
                continue
            path = self.resource.operations[i][1]
            break
        else:
            return

        m = re.search(r"{([-\w]+)}$", path)
        if m:
            return m.group(1)

    def parameters(self):
        def itera(operationId):
            for parameter in AnsibleModule._flatten_parameter(
                self.resource.operations[operationId][2], self.definitions
            ):
                name = parameter["name"]
                if name == "spec":
                    for i in parameter["subkeys"]:
                        yield i
                else:
                    yield parameter

        results = {}
        for operationId in self.default_operationIds:
            if operationId not in self.resource.operations:
                continue

            for parameter in sorted(
                itera(operationId),
                key=lambda item: (item["name"], item.get("description")),
            ):
                name = parameter["name"]
                if name not in results:
                    results[name] = parameter
                    results[name]["operationIds"] = []

                if "description" not in parameter:
                    pass
                elif "description" not in results[name]:
                    results[name]["description"] = parameter.get("description")
                elif results[name]["description"] != parameter.get("description"):
                    # We can hardly merge two description strings and
                    # get magically something meaningful
                    if len(parameter["description"]) > len(
                        results[name]["description"]
                    ):
                        results[name]["description"] = parameter["description"]
                if "enum" in parameter:
                    if "enum" not in results[name]:
                        results[name]["enum"] = parameter["enum"]
                    else:
                        results[name]["enum"] += parameter["enum"]

                results[name]["operationIds"].append(operationId)
                results[name]["operationIds"].sort()

        for name, result in results.items():
            if result.get("enum"):
                enums = []
                for item in result["enum"]:
                    enums.append(str(item))
                result["enum"] = sorted(set(enums))
            if result.get("required"):
                if "description" in result:
                    result["description"] += "\nRequired with I(state={})".format(
                        sorted(set(result["operationIds"]))
                    )
                else:
                    result["description"] = "Required with I(state={})".format(
                        sorted(set(result["operationIds"]))
                    )
                del result["required"]

        results["state"] = {
            "name": "state",
            "default": "present",
            "type": "str",
            "enum": sorted(list(self.default_operationIds)),
        }

        return sorted(results.values(), key=lambda item: item["name"])

    def gen_url_func(self, app):
        first_operation = list(self.resource.operations.values())[0]
        path = first_operation[1]
        basePath = first_operation[3]
        url_func = ast.parse(
            self.URL.format(app=app, path=path, basePath=basePath)
        ).body[0]
        return url_func

    @staticmethod
    def _property_to_parameter(prop_struct, definitions):

        required_keys = prop_struct.get("required", [])
        try:
            properties = prop_struct["properties"]
        except KeyError:
            return prop_struct

        for name, v in properties.items():
            parameter = {
                "name": name,
                "type": v.get("type", "str"),  # 'str' by default, should be ok
                "description": v.get("description", ""),
                "required": True if name in required_keys else False,
            }

            if "$ref" in v:
                ref = definitions.get(v)
                if "properties" in ref:
                    unsorted_subkeys = AnsibleModule._property_to_parameter(
                        definitions.get(v), definitions
                    )
                    parameter["type"] = "dict"
                    subkeys = sorted(unsorted_subkeys, key=lambda item: item["name"])
                    parameter["subkeys"] = list(subkeys)
                else:
                    for k, v in ref.items():
                        parameter[k] = v

            yield parameter

    @staticmethod
    def _flatten_parameter(parameter_structure, definitions):
        for i in parameter_structure:
            if "schema" in i:
                if "$ref" in i["schema"]:
                    schema = definitions.get(i["schema"])
                    for j in AnsibleModule._property_to_parameter(schema, definitions):
                        yield j
                elif "type" in i["schema"]:
                    i["type"] = i["schema"]["type"]
                    yield i
            else:
                yield i

    def in_query_parameters(self):
        return [p["name"] for p in self.parameters() if p.get("in") == "query"]

    def renderer(self, target_dir, vendor, app):
        DEFAULT_MODULE = """
_HEADER='''
#!/usr/bin/env python
# Info module template

#############################################
#                WARNING                    #
#############################################
#
# This file is auto generated by
#   https://github.com/jgroom33/vmware_rest_code_generator
#
# Do not edit this file manually.
#
# Changes should be made in the swagger used to
#   generate this file or in the generator
#
#############################################
'''
from __future__ import absolute_import, division, print_function
__metaclass__ = type
import socket
import json

DOCUMENTATION = ""

IN_QUERY_PARAMETER = None


from ansible.module_utils.basic import env_fallback
try:
    from ansible_module.turbo.module import AnsibleTurboModule as AnsibleModule
except ImportError:
    from ansible.module_utils.basic import AnsibleModule
from ansible_collections.{vendor}.{app}.plugins.module_utils.{app} import (
    gen_args,
    open_session,
    update_changed_flag)


def prepare_argument_spec():
    argument_spec = {{
        "{app}_hostname": dict(
            type='str',
            required=False,
            fallback=(env_fallback, ['{APP}_HOST']),
        ),
        "{app}_username": dict(
            type='str',
            required=False,
            fallback=(env_fallback, ['{APP}_USER']),
        ),
        "{app}_password": dict(
            type='str',
            required=False,
            no_log=True,
            fallback=(env_fallback, ['{APP}_PASSWORD']),
        )
    }}

    return argument_spec

async def main( ):
    module_args = prepare_argument_spec()
    module = AnsibleModule(argument_spec=module_args, supports_check_mode=True)
    session = await open_session({app}_hostname=module.params['{app}_hostname'], {app}_username=module.params['{app}_username'], {app}_password=module.params['{app}_password'])
    result = await entry_point(module, session)
    module.exit_json(**result)

def url(params):
    pass

def entry_point():
    pass

if __name__ == '__main__':
    import asyncio
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())

"""
        syntax_tree = ast.parse(
            DEFAULT_MODULE.format(vendor=vendor, app=app, APP=app.upper())
        )
        arguments = gen_arguments_py(self.parameters(), self.list_index())
        documentation = format_documentation(
            gen_documentation(self.name, self.description, self.parameters())
        )
        url_func = self.gen_url_func(app)
        entry_point_func = self.gen_entry_point_func(app)

        in_query_parameters = self.in_query_parameters()

        class SumTransformer(ast.NodeTransformer):
            def visit_FunctionDef(self, node):
                return node

            def visit_FunctionDef(self, node):
                if node.name == "url":
                    node.body[0] = url_func
                elif node.name == "entry_point":
                    node = entry_point_func
                elif node.name == "prepare_argument_spec":
                    for arg in arguments:
                        node.body.insert(1, arg)
                return node

            def visit_Assign(self, node):
                if not isinstance(node.targets[0], ast.Name):
                    pass
                elif node.targets[0].id == "IN_QUERY_PARAMETER":
                    node.value = ast.Str(in_query_parameters)
                return node

        syntax_tree = SumTransformer().visit(syntax_tree)
        syntax_tree = ast.fix_missing_locations(syntax_tree)

        module_dir = target_dir
        module_dir.mkdir(parents=True, exist_ok=True)
        module_py_file = module_dir / "{name}.py".format(name=self.name)
        with module_py_file.open("w") as fd:
            for l in astunparse.unparse(syntax_tree).split("\n"):
                if l.startswith("DOCUMENTATION ="):
                    fd.write(documentation)
                elif l.startswith("_HEADER ="):
                    header_lines = l.split("\\n")
                    for header_line in header_lines[1:-1]:
                        fd.write(header_line)
                        fd.write("\n")
                else:
                    fd.write(l)
                fd.write("\n")


class AnsibleModule(AnsibleModuleBase):

    URL = """
return "{{{app}_hostname}}{basePath}{path}".format(**params)
"""

    def __init__(self, resource, definitions):
        super().__init__(resource, definitions)
        self.default_operationIds = set(list(self.resource.operations.keys()))

    def gen_entry_point_func(self, app):
        MAIN_FUNC = """
async def entry_point(module, session):
    func = globals()["_" + module.params['state']]
    return await func(module.params, session)
"""
        main_func = ast.parse(MAIN_FUNC.format(name=self.name))

        for operation in sorted(self.default_operationIds):
            (verb, path, _, basePath) = self.resource.operations[operation]

            FUNC_NO_DATA_TPL = """
async def _{operation}(params, session):
    _url = "{{{app}_hostname}}{basePath}{path}".format(**params) + gen_args(params, IN_QUERY_PARAMETER)
    async with session.{verb}(_url) as resp:
        content_types = ['application/json-patch+json', 'application/vnd.api+json', 'application/json']
        try:
            if resp.headers["Content-Type"] in content_types:
                _json = await resp.json()
            else:
                print("response Content-Type not supported")
        except KeyError:
            _json = {{}}
        return await update_changed_flag(_json, resp.status, "{operation}")
"""
            FUNC_WITH_DATA_TPL = """
async def _{operation}(params, session):
    accepted_fields = []

    spec = {{}}
    for i in accepted_fields:
        if params[i] is not None:
            spec[i] = params[i]
    _url = "{{{app}_hostname}}{basePath}{path}".format(**params) + gen_args(params, IN_QUERY_PARAMETER)
    async with session.{verb}(_url, json=spec) as resp:
        content_types = ['application/json-patch+json', 'application/vnd.api+json', 'application/json']
        try:
            if resp.headers["Content-Type"] in content_types:
                _json = await resp.json()
            else:
                print("response Content-Type not supported")
        except KeyError:
            _json = {{}}

        return await update_changed_flag(_json, resp.status, "{operation}")
"""

            data_accepted_fields = []
            for p in self.parameters():
                if "operationIds" in p:
                    if operation in p["operationIds"]:
                        if not p.get("in") in ["path", "query"]:
                            data_accepted_fields.append(p["name"])
                        elif operation in ["post", "patch", "put"]:
                            data_accepted_fields.append(p["name"])

            if data_accepted_fields:
                func = ast.parse(
                    FUNC_WITH_DATA_TPL.format(
                        app=app,
                        operation=operation,
                        verb=verb,
                        path=path,
                        basePath=basePath,
                    )
                ).body[0]
                func.body[0].value.elts = [
                    ast.Constant(value=i, kind=None)
                    for i in sorted(data_accepted_fields)
                ]
            else:
                code = FUNC_NO_DATA_TPL.format(
                    app=app,
                    operation=operation,
                    verb=verb,
                    path=path,
                    basePath=basePath,
                )
                func = ast.parse(code).body[0]

            main_func.body.append(func)

        return main_func.body


class Definitions:
    def __init__(self, data):
        super().__init__()
        self.definitions = data

    @staticmethod
    def _ref_to_dotted(ref):
        return ref["$ref"].split("/")[2]

    def get(self, ref):
        dotted = self._ref_to_dotted(ref)
        v = self.definitions[dotted]
        return v


class Path:
    def __init__(self, path, value, basepath):
        super().__init__()
        self.path = path
        self.basePath = basepath
        self.operations = {}
        self.verb = {}
        self.value = value

    def summary(self, verb):
        return self.value[verb]["summary"]


class SwaggerFile:
    def __init__(self, file_path):
        super().__init__()
        self.resources = {}
        with file_path.open() as fd:
            json_content = json.load(fd)
            base_path = json_content.get("basePath", "")
            if base_path == "/":
                base_path = ""
            self.basePath = base_path
            self.definitions = Definitions(json_content.get("definitions", {}))
            self.paths = self.load_paths(json_content["paths"], base_path)

    @staticmethod
    def load_paths(paths, base_path=""):
        result = {}

        for path in [Path(p, v, base_path) for p, v in paths.items()]:
            if path not in paths:
                result[path.path] = path
            for verb, desc in path.value.items():
                operationId = verb
                path.operations[operationId] = (
                    verb,
                    path.path,
                    desc.get("parameters", {}),
                    base_path,
                )
        return result

    @staticmethod
    def init_resources(paths):
        resources = {}
        for path in paths:
            name = path_to_name(path.path)
            if name not in resources:
                resources[name] = Resource(name)
                resources[name].description = ""

            for verb, v in path.operations.items():
                if verb in resources[name].operations:
                    raise Exception(
                        "operationId already defined: %s vs %s"
                        % (resources[name].operations[verb], v)
                    )
                resources[name].operations[verb] = v
        return resources


def main():

    vendors = next(os.walk("src/swagger"))[1]
    for vendor in vendors:
        apps = next(os.walk("src/swagger/%s" % vendor))[1]
        for app in apps:
            module_list = []
            p = pathlib.Path("src/swagger/%s/%s" % (vendor, app))
            for json_file in p.glob("*.json"):
                print("Generating modules from {}".format(json_file))
                swagger_file = SwaggerFile(json_file)
                resources = swagger_file.init_resources(swagger_file.paths.values())

                for resource in resources.values():
                    module = AnsibleModule(
                        resource, definitions=swagger_file.definitions
                    )
                    if len(module.default_operationIds) > 0:
                        module.renderer(
                            pathlib.Path(
                                pathlib.Path("build")
                                / vendor
                                / app
                                / "plugins"
                                / "modules"
                            ),
                            vendor,
                            app,
                        )
                        module_list.append(module.name)


if __name__ == "__main__":
    main()
