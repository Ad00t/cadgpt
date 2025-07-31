import os
import json
import jsonref
from openapi_schema_to_json_schema import to_json_schema
import threading
import copy 
import math

def remove_circular_refs(ob, _seen=None):
    if _seen is None:
        _seen = set()
    if id(ob) in _seen:
        # circular reference, remove it.
        return None
    _seen.add(id(ob))
    res = ob
    if isinstance(ob, dict):
        res = {
            remove_circular_refs(k, _seen): remove_circular_refs(v, _seen)
            for k, v in ob.items()}
    elif isinstance(ob, (list, tuple, set, frozenset)):
        res = type(ob)(remove_circular_refs(v, _seen) for v in ob)
    # remove id again; only *nested* references count
    _seen.remove(id(ob))
    return res

def convert_schemas_chunk(raw_schemas, new_schemas, chunk):
    thread_name = threading.current_thread().name
    for i, schema_name in enumerate(chunk):
        if i % 10 == 0:
            print(f'{thread_name} -- {i}/{len(chunk)}')
        new_schemas[schema_name] = to_json_schema(raw_schemas[chunk[i]])
    print(f'{thread_name} -- done')


def merge_dicts(a, b):
    """Recursively merge dictionary b into a."""
    result = copy.deepcopy(a)
    for key, value in b.items():
        if key in result:
            if isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = merge_dicts(result[key], value)
            elif isinstance(result[key], list) and isinstance(value, list):
                # Merge lists like "required" or "enum", avoid duplicates
                result[key] = list(dict.fromkeys(result[key] + value))
            else:
                # If both values conflict, prefer b
                result[key] = value
        else:
            result[key] = value
    return result

def flatten_allof(schema):
    """Recursively flatten allOf entries in a schema dict."""
    if isinstance(schema, dict):
        schema = copy.deepcopy(schema)

        # Flatten children first
        for key in list(schema.keys()):
            schema[key] = flatten_allof(schema[key])

        if "allOf" in schema:
            merged = {}
            for sub_schema in schema["allOf"]:
                flattened_sub = flatten_allof(sub_schema)
                merged = merge_dicts(merged, flattened_sub)
            # Merge remainder of schema with flattened result
            schema.pop("allOf")
            schema = merge_dicts(merged, schema)

    elif isinstance(schema, list):
        schema = [flatten_allof(item) for item in schema]

    return schema

def fix_schema(schema):
    """Modify schema: add required, restrict additionalProperties, set default array items."""
    schema = flatten_allof(schema)  # Step 1: Flatten allOf first

    if not isinstance(schema, dict):
        return schema

    if schema.get('type') == 'object':
        props = schema.get('properties', {})
        schema['additionalProperties'] = False
        schema['required'] = list(props.keys())
        for key in props:
            props[key] = fix_schema(props[key])

    elif schema.get('type') == 'array':
        if not schema.get('items'):
            schema['items'] = { '$ref': '#/$defs/feature' }
        elif isinstance(schema['items'], list):
            schema['items'] = [fix_schema(item) for item in schema['items']]
        else:
            schema['items'] = fix_schema(schema['items'])

    return schema

if __name__ == '__main__':
    if not os.path.exists('llm_static/resolved_schemas.json'):
        with open('llm_static/openapi_schemas.json', 'r') as in_file:
            openapi_json = json.loads(in_file.read())

        raw_schemas = openapi_json['components']['schemas']
        schema_names = list(raw_schemas.keys())
        n_schemas = len(schema_names)
        n_threads = 10
        n_schemas_per_thread = math.ceil(len(schema_names) / n_threads)
        threads = []
        new_schemas = {}
        start = 0
        for i in range(n_threads):
            chunk_size = n_schemas // n_threads + (1 if i < n_schemas % n_threads else 0)
            chunk = schema_names[start:start+chunk_size]
            t = threading.Thread(target=convert_schemas_chunk, args=(raw_schemas, new_schemas, chunk,), name=f'thread-{start}:{start+chunk_size}', daemon=True)
            threads.append(t)
            t.start()
            start += chunk_size 
        for i in range(n_threads):
            threads[i].join()

        jsonschema_json = copy.deepcopy(openapi_json)
        jsonschema_json['components']['schemas'] = new_schemas
        inlined = jsonref.replace_refs(
            obj=jsonschema_json,
            jsonschema=True,
            proxies=False,
            merge_props=True,
            lazy_load=False
        )

        with open('llm_static/resolved_schemas.json', 'w') as out_file:
            jsonref.dump(remove_circular_refs(inlined), out_file)
 
    with open('llm_static/final_schema_template.json', 'r') as template_file, \
        open('llm_static/resolved_schemas.json', 'r') as schemas_file:

        schemas = json.load(schemas_file)['components']['schemas']
        final_schema = json.load(template_file)
    
    include_schemas = [ 'BTMFeature-134', 'BTMSketch-151' ]

    with open(f'llm_static/final_schema.json', 'w') as out_file:
        for schema_name in include_schemas:
            inc_schema = schemas[schema_name]
            inc_schema['properties']['btType'] = { 'type': 'string', 'const': schema_name } 
            fixed = fix_schema(inc_schema)
            final_schema['$defs']['feature']['anyOf'].append(fixed) 
        
        json.dump(final_schema, out_file, indent=2)
