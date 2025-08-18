import os
import json
import jsonref
from openapi_schema_to_json_schema import to_json_schema
import threading
import copy 
import math

def convert_schemas_chunk(raw_schemas, new_schemas, chunk):
    thread_name = threading.current_thread().name
    for i, schema_name in enumerate(chunk):
        if i % 10 == 0:
            print(f'{thread_name} -- {i}/{len(chunk)}')
        new_schemas[schema_name] = to_json_schema(raw_schemas[chunk[i]], { 'keepNotSupported': [ 'discriminator' ] })
    print(f'{thread_name} -- done')


def merge_dicts(a, b):
    result = copy.deepcopy(a)
    for key, value in b.items():
        if key in result:
            if isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = merge_dicts(result[key], value)
            elif isinstance(result[key], list) and isinstance(value, list):
                result[key] = list(dict.fromkeys(result[key] + value))
            else:
                result[key] = value
        else:
            result[key] = value
    return result

def resolve_schema_pointer(root, path):
    """
    Return the actual object from root for a JSON Pointer-like path
    (e.g. '#/components/schemas/MyType' or '/components/schemas/MyType').
    This returns the object *in* root (no deepcopy) so id() identity is preserved.
    """
    # print('resolving pointer:', path)
    if path.startswith('#'):
        path = path[1:]
    # handle leading slash
    parts = [p for p in path.split('/') if p]
    ref = root
    for part in parts:
        if isinstance(ref, dict) and part in ref:
            ref = ref[part]
        else:
            raise KeyError(f"Could not resolve path {path} at {part}")
    return ref

def flatten_schema(root, schema, _seen=None, _memo=None):
    """
    Flatten schema (convert discriminator -> oneOf), preventing cycles.

    Parameters:
      - root: the full document (so resolve_schema_path can find mappings)
      - schema: the current schema object (usually a dict, list, or primitive)
      - _seen: set of seen oids
      - _memo: dict mapping id(original_obj) -> flattened result (reused)
    """

    if not _seen:
        _seen = set()

    if not _memo:
        _memo = {}

    if not isinstance(schema, (dict, list)):
        return schema

    if isinstance(schema, list):
        return [ flatten_schema(root, item, _seen, _memo) for item in schema ]

    oid = id(schema)
    # print('oid:', oid, '_memo:', len(list(_memo.keys())))
    # print(json.dumps(schema, indent=2))

    if oid in _memo:
        return _memo[oid]

    if oid in _seen:
        return {}

    _seen.add(oid)

    out = {}
    base_keys = [ k for k in list(schema.keys()) if k not in  [ 'allOf', 'discriminator' ] ]
    if len(base_keys) > 0:
        for k in base_keys:
            out[k] = flatten_schema(root, schema[k], _seen, _memo)

    if 'allOf' in schema:
        merged = {}
        out = { 'anyOf': [ out.copy() ] }
        for sub_schema in schema['allOf']:
            flat_sub = flatten_schema(root, sub_schema, _seen, _memo)
            if isinstance(flat_sub, dict):
                if '$ref' in flat_sub:
                    out['anyOf'].append({ '$ref': flat_sub['$ref'] })
                else:
                    merged = merge_dicts(merged, flat_sub)
        out = merge_dicts(out, merged)
        # print(list(out.get('properties', {}.keys())))
        # print('allOf:', json.dumps(out, indent=2))

    if 'discriminator' in schema and 'mapping' in schema['discriminator']:
        if 'anyOf' not in out:
            out = { 'anyOf': [ out.copy() ] }
        for disc_val, pointer in schema['discriminator']['mapping'].items():
            out['anyOf'].append({ '$ref': pointer }) 
        # print('discriminator:', json.dumps(out, indent=2))
    
    _memo[oid] = out 
    return out 

def fix_schema(schema, schema_name=None, depth=0):
    if isinstance(schema, list):
        # Fix each item, remove None entries
        fixed_list = [ fix_schema(item, schema_name, depth+1) for item in schema ]
        return [ item for item in fixed_list if item is not None ]

    if not isinstance(schema, dict):
        return schema

    # Reref
    if '$ref' in schema and '$defs' not in schema['$ref']:
        target = schema['$ref'].split('/')[-1]
        schema['$ref'] = f'#/$defs/{target}'

    # Remove ignored keys from object properties
    if schema.get('type') == 'object':
        properties = schema.get('properties', {})
        ignore_keys = {
            'importMicroversion', 'namespace', 'nodeId',
            'suppressionConfigured', 'suppressionState'
        }
        for k in list(properties.keys()):
            if k in ignore_keys:
                del properties[k]
            else:
                fixed_subschema = fix_schema(properties[k])
                if fixed_subschema is None:
                    del properties[k]
                else:
                    properties[k] = fixed_subschema

        # Delete this object schema if no properties remain
        if not properties:
            return None

        schema['properties'] = properties
        schema['additionalProperties'] = False
        schema['required'] = list(properties.keys())
       
        # if depth <= 2 and schema_name is not None and 'btType' in schema['required']:
        #     schema['properties']['btType'] = { 'type': 'string', 'const': schema_name }

    # Fix arrays
    elif schema.get('type') == 'array':
        if 'uniqueItems' in schema:
            del schema['uniqueItems']
        if 'items' not in schema or schema['items'] is None:
            schema['items'] = { '$ref': '#/$defs/feature' }
        else:
            fixed_items = fix_schema(schema['items'])
            if fixed_items is None:
                del schema['items']
            else:
                schema['items'] = fixed_items

    # Recursively fix nested schemas or schema arrays in other keys
    schema_keys = {'allOf', 'anyOf', 'oneOf', '$defs', 'definitions', 'properties', 'items'}
    for k, v in list(schema.items()):
        if k in schema_keys:
            fixed_sub = fix_schema(v, schema_name, depth+1)
            if fixed_sub is None:
                del schema[k]
            else:
                schema[k] = fixed_sub
        elif k not in {'required', 'type', 'additionalProperties'}:
            fixed_sub = fix_schema(v, schema_name, depth+1)
            if fixed_sub is None:
                del schema[k]
            else:
                schema[k] = fixed_sub

    # Edge cases
    if schema.get('type') in [ 'number', 'string' ] and 'format' in schema:
        del schema['format']

    return schema

def find_relevant_schemas(root, schema, out):
    if isinstance(schema, list):
        for item in schema:
            find_relevant_schemas(root, item, out)
    elif isinstance(schema, dict):
        for k, v in schema.items():
            find_relevant_schemas(root, v, out)
    elif isinstance(schema, str):
        if schema.startswith('#'):
            ref_name = schema.split('/')[-1]
            if not ref_name in out:
                out.add(ref_name)
                ref_schema = resolve_schema_pointer(root, schema)
                find_relevant_schemas(root, ref_schema, out)

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

        with open('llm_static/resolved_schemas.json', 'w') as out_file:
            jsonref.dump(jsonschema_json, out_file)
            print('resolved_schemas.json created')
 
    with open('llm_static/final_schema_template.json', 'r') as template_file, \
        open('llm_static/resolved_schemas.json', 'r') as schemas_file:
        root = json.load(schemas_file)
        print('resolved_schemas.json loaded')
        final_schema = json.load(template_file)
        print('final_schema_template.json loaded')

    print('Finding relevant schemas')
    relevant_schema_names = set()
    find_relevant_schemas(root, root['components']['schemas']['BTMFeature-134'], relevant_schema_names)
    print(f'Relevant schemas ({len(relevant_schema_names)}):', relevant_schema_names)

    with open(f'llm_static/final_schema.json', 'w') as out_file:
        for schema_name in relevant_schema_names:
            schema = root['components']['schemas'][schema_name]
            refactored_schema = flatten_schema(root, schema)
            refactored_schema = fix_schema(refactored_schema, schema_name)
            final_schema['$defs'][schema_name] = refactored_schema

        # More edge cases
        final_schema['$defs']['Lines'] = { 'type': 'array', 'items': { 'type': 'string' } }

        json.dump(final_schema, out_file)
        print('final_schema.json created')
