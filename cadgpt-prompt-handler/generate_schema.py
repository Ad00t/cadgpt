import json
import logging
import boto3
import os
import sys
import dotenv
import time
from openai import OpenAI
from qdrant_client import QdrantClient
from pymongo.synchronous.database import Database
from pymongo import MongoClient

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

if os.environ['ENV'] == 'dev': 
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)

lambda_client = boto3.client('lambda')
ssm = boto3.client('ssm')

openai_client: OpenAI | None = None
vector_store: QdrantClient | None = None
doc_db: Database | None = None

def lambda_handler(event, context):
    log_prefix = f'lambda_handler()'
    logger.info(f'{log_prefix} {json.dumps(event)} {context}')

    init_clients()
    if openai_client is None or vector_store is None or doc_db is None:
        logger.error(f'{log_prefix}: clients not initialized {str(openai_client)} {str(vector_store)} {str(doc_db)}')
        return { 'statusCode': 500, 'body': json.dumps({ 'message': 'clients not initialized' })}

    try:
        body = json.loads(event['body'])
        prompt = body['prompt'].strip()
        logger.info(f'{log_prefix}: prompt: "{prompt}"')    
        doc_id = body['doc_id'].strip()
        logger.info(f'{log_prefix}: doc_id: "{doc_id}"')       
    except Exception as e: 
        logger.error(f'{log_prefix}: prompt error: ', exc_info=True)
        return { 'statusCode': 400, 'body': json.dumps({ 'message': 'bad prompt' }) }

    if os.environ['SLOW_START'].lower() == 'true':
        onshape_doc_search(query=prompt)

    rag_context = generate_rag_context(prompt=prompt)
    generate_llm_response(rag_context=rag_context, prompt=prompt, doc_id=doc_id)
    
    logger.info(f'{log_prefix}: done')
    return { 'statusCode': 200, 'body': json.dumps({ 'message': 'done' }) }

def init_clients():
    log_prefix = f'init_clients()'
    global openai_client, vector_store, doc_db
    if not (openai_client is None or vector_store is None or doc_db is None):
        logger.info(f'{log_prefix}: clients already initialized')
        return
    
    os.environ['OPENAI_API_KEY'] = ssm.get_parameter(Name='OPENAI_API_KEY', WithDecryption=True)['Parameter']['Value']
    os.environ['QDRANT_API_KEY'] = ssm.get_parameter(Name='QDRANT_API_KEY', WithDecryption=True)['Parameter']['Value']
    os.environ['CADGPT_DOC_DB_PASS'] = ssm.get_parameter(Name='CADGPT_DOC_DB_PASS', WithDecryption=True)['Parameter']['Value'] 
    logger.info(f'{log_prefix}: credentials retrieved')

    openai_client = OpenAI(
        api_key=os.environ['OPENAI_API_KEY']
    )
    logger.info(f'{log_prefix}: llm initialized')

    vector_store = QdrantClient(
        url=os.environ['VECTOR_STORE_URL'],
        api_key=os.environ['QDRANT_API_KEY'],
    )
    logger.info(f'{log_prefix}: vector store initialized')

    doc_db = MongoClient(
        host=f"mongodb://{os.environ['DOC_DB_USER']}:{os.environ['CADGPT_DOC_DB_PASS']}@{os.environ['DOC_DB_HOST']}:{os.environ['DOC_DB_PORT']}/?ssl=true&retryWrites=false",
        tls=True,   
        tlsCAFile='global-bundle.pem'
    )[os.environ['DOC_DB_NAME']]  
    logger.info(f'{log_prefix}: {doc_db.name} initialized')

def onshape_doc_search(query):
    log_prefix = f'onshape_doc_search()'
    try:
        payload = json.dumps({
            'query': query,
            'n_docs': int(os.environ['N_DOCS_PER_QUERY'])
        })
        lambda_client.invoke(
            FunctionName='cadgpt-onshape-doc-search',
            InvocationType='Event', # Asynchronous
            Payload=payload
        )
        logger.info(f'{log_prefix}: lambda invoked -- {payload}')
    except Exception as e:
        logger.error(f'{log_prefix}: failed: ', exc_info=True)

def generate_rag_context(prompt) -> str:
    log_prefix = f'generate_rag_context()'
    try:
        global openai_client, vector_store

        prompt_vec = openai_client.embeddings.create(
            input=prompt,
            model=os.environ['EMBEDDING_MODEL']
        ).data[0].embedding

        search_result = vector_store.search(
            collection_name='partstudio_features',
            query_vector=prompt_vec,
            limit=int(os.environ['MAX_CONTEXT_EXAMPLES'])
        )
        logger.info(f'{log_prefix}: {len(search_result)} parts found\n{json.dumps([ f"{p.id} -- {p.payload['name']}" for p in search_result ])}')
        
        rag_context = ''
        for i, rag_point in enumerate(search_result):
            try:
                logger.debug(f'{log_prefix}: RAG point {i}:\n\n{rag_point}')
                rag_doc = doc_db['partstudio_features'].find_one({ '__id': rag_point.id })
                rag_context += f'### EXAMPLE {i+1}\n\n{rag_doc}\n\n'
            except Exception as e:
                logger.error(f'{log_prefix}: RAG document retrieval failed: ', exc_info=True)
        return rag_context
    except Exception as e:
        logger.error(f'{log_prefix}: failed: ', exc_info=True)
    return ''

def generate_llm_response(rag_context, prompt, doc_id):
    log_prefix = f'generate_llm_response()'
    logger.info(f'{log_prefix}: llm: {os.environ['LLM']}')

    global openai_client

    with open('llm_static/instructions_template.txt') as template_file, \
         open('llm_static/features_api_doc.txt') as api_doc_file:
        template = template_file.read()
        api_doc =  api_doc_file.read()

    instructions = template.format(
        api_doc=api_doc,
        rag_context=rag_context
    )

    logger.info(f'{log_prefix}: context: ~{len(instructions.split(' '))} tokens')
    logger.debug(f'{log_prefix}: {instructions}')

    with open('llm_static/final_schema.json') as in_file:
        schema = json.load(in_file)
        logger.debug(f'{log_prefix}: schema loaded:\n\n{json.dumps(schema)}')

    input_list = [
        { 'role': 'developer', 'content': instructions },
        { 'role': 'developer', 'content': '' },
        { 'role': 'developer', 'content': f'Generate the next feature object in this design given the user prompt: {prompt}' }
    ]

    start_ts = time.time()
    for step in range(int(os.environ['MAX_FEATURES'])):
        try:
            curr_features = json.loads(json.load(
                lambda_client.invoke(
                    FunctionName='cadgpt-onshape-api',
                    InvocationType='RequestResponse',  # synchronous
                    Payload=json.dumps({
                        'endpoint': 'get_features',
                        'payload': {
                            'doc_id': doc_id, 
                        }
                    }),
                )['Payload']
            )['body'])

            if 'features' not in curr_features:
                logger.error(f'{log_prefix}: current features retrieval failed: {json.dumps(curr_features)}')
                break

            logger.debug(f'{log_prefix}: curr features: {json.dumps(curr_features)}')
            input_list[1]['content'] = f'# EXISTING PARTSTUDIO FEATURES\n\n{json.dumps(curr_features)}\n\n'
            logger.debug(f'{log_prefix}: input list: {json.dumps(input_list)}')

            step_start_ts = time.time()
            response = openai_client.responses.create(
                model=os.environ['LLM'],
                input=input_list,
                text={
                    'format': {
                        'type': 'json_schema',
                        'name': 'feature_definition',
                        'strict': True,
                        'schema': schema 
                    }
                }
            )
            step_end_ts = time.time()
            
            input_list.append({ 'role': 'assistant', 'content': response.output_text })
            response_json = json.loads(response.output_text)

            logger.info(f'{log_prefix} step {step} {response_json['metadata']['call_type']} ({int(step_end_ts - step_start_ts)}s): {json.dumps(response_json)}')
            if response_json['metadata']['done']:
                logger.info(f'{log_prefix}: done ({int(step_end_ts - start_ts)}s total)')
                break

            onshape_res = handle_llm_response(response_json, doc_id, step)
            input_list.append({ 'role': 'developer', 'content': f'Onshape response: {json.dumps(onshape_res)}' })
        except Exception as e:
            logger.error(f'{log_prefix}: failed: ', exc_info=True)
            break

def handle_llm_response(response_json, doc_id, step):
    log_prefix = f'handle_llm_response()'
    try:
        call_type = response_json['metadata']['call_type']
        onshape_payload = { 'doc_id': doc_id }
        match call_type:
            case 'add_feature':
                if 'featureId' in response_json['feature']:
                    del response_json['feature']['featureId']
                onshape_payload.update({
                    'feature': response_json['feature'] 
                })
            case 'update_feature':
                onshape_payload.update({
                    'feature': response_json['feature'],
                    'feature_id': response_json['feature']['featureId']
                })
            case 'delete_feature':
                onshape_payload.update({
                    'feature_id': response_json['feature']['featureId']
                })
        
        lambda_payload = json.dumps({
            'endpoint': call_type,
            'payload': onshape_payload
        })
        onshape_res = json.load(
            lambda_client.invoke(
                FunctionName='cadgpt-onshape-api',
                InvocationType='RequestResponse',  # synchronous
                Payload=lambda_payload,
            )['Payload']
        )
        logger.info(f"{log_prefix}: onshape res: {onshape_res.get('statusCode')} {onshape_res.get('body')}")
        return onshape_res
    except Exception as e:
        logger.error(f'{log_prefix}: failed: ', exc_info=True)
        return

if os.environ['ENV'] == 'dev':
    dotenv.load_dotenv('.env')
    with open('test_event.json', 'r') as test_event_file:
        test_event = json.load(test_event_file)
        lambda_handler(test_event, {})
