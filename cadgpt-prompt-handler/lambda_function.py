import json
import time
import logging
import boto3
import os
import sys
import dotenv
import re
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
        logger.error(f'{log_prefix}: clients not initialized')
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
        onshape_doc_search(prompt)

    if os.environ['SHOULD_ENRICH_PROMPT'].lower() == 'true':
        prompt = enrich_prompt(prompt)

    rag_context = generate_rag_context(prompt)
    generate_features(rag_context, prompt, doc_id)

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
    logger.info(f'{log_prefix}: OpenAI client initialized')

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
            'n_docs': os.environ['N_DOCS_PER_QUERY']
        })
        lambda_client.invoke(
            FunctionName='cadgpt-onshape-doc-search',
            InvocationType='Event', # Asynchronous
            Payload=payload
        )
        logger.info(f'{log_prefix}: lambda invoked -- {payload}')
    except Exception as e:
        logger.error(f'{log_prefix}: failed: ', exc_info=True)

def enrich_prompt(prompt) -> str:
    log_prefix = f'enrich_prompt()'
    try:
        global openai_client

        with open('llm_static/prompt_enricher_instructions_template.txt') as template_file:
            template = template_file.read()

        enricher_instr = template
        enricher_input = [
            { 'role': 'developer', 'content': enricher_instr },
            { 'role': 'user', 'content': prompt }
        ]
        logger.debug(f'{log_prefix}: {json.dumps(enricher_input)}')

        start_ts = time.time()
        response = openai_client.responses.create(
            model='gpt-5-nano',
            reasoning={ 'effort': 'minimal' },
            input=enricher_input
        )
        end_ts = time.time()

        logger.info(f'{log_prefix}: {response.model} ({int(end_ts - start_ts)}s): {response.output_text}')
        return response.output_text
    except Exception as e:
        logger.error(f'{log_prefix}: failed: ', exc_info=True)
    return prompt

def generate_rag_context(prompt) -> str:
    log_prefix = f'generate_rag_context()'
    try:
        global openai_client, vector_store, doc_db

        prompt_vec = openai_client.embeddings.create(
            input=prompt,
            model='text-embedding-3-small'
        ).data[0].embedding

        search_result = vector_store.search(
            collection_name='partstudio_features',
            query_vector=prompt_vec,
            limit=int(os.environ['MAX_CONTEXT_EXAMPLES'])
        )
        logger.info(f'{log_prefix}: {len(search_result)} docs found\n{json.dumps([ f"{p.id} -- {p.payload['name']}" for p in search_result ])}')

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

def generate_features(rag_context, prompt, doc_id):
    log_prefix = f'generate_features()'
    global openai_client

    with open('llm_static/feature_generator_instructions_template.txt') as generator_template_file, \
         open('llm_static/features_api_doc.txt') as api_doc_file, \
         open('llm_static/final_schema.json') as schema_file, \
         open('llm_static/step_recommender_instructions_template.txt') as recommender_template_file:
        generator_template = generator_template_file.read()
        api_doc =  api_doc_file.read()
        schema = json.load(schema_file)
        recommender_template = recommender_template_file.read()

    generator_instr = generator_template.format(
        api_doc=api_doc,
        rag_context=rag_context,
        max_steps=os.environ['MAX_STEPS']
    )
    generator_input = [
        { 'role': 'developer', 'content': generator_instr },
        { 'role': 'developer', 'content': '' },
        { 'role': 'user', 'content': prompt }
    ]

    recommender_instr = recommender_template
    recommender_input = [
        { 'role': 'developer', 'content': recommender_instr },
        { 'role': 'developer', 'content': '' },
        { 'role': 'user', 'content': prompt },
    ]

    start_ts = time.time()
    for step in range(1, int(os.environ['MAX_STEPS']) + 1):
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

            existing_features = f'# EXISTING PARTSTUDIO FEATURES\n\n{json.dumps(curr_features)}\n\n'
            generator_input[1]['content'] = existing_features
            recommender_input[1]['content'] = existing_features

            recommendation = recommend_step(step, recommender_input)
            generator_input.append({ 'role': 'developer', 'content': f'Recommendation for step {step}:\n\n {recommendation}' })
            recommender_input.append({ 'role': 'assistant', 'content': recommendation })

            step_start_ts = time.time()
            response = openai_client.responses.create(
                model='gpt-5-mini',
                input=generator_input, # type: ignore
                # reasoning={ 'effort': 'low' },
                text={
                    'format': {
                        'type': 'json_schema',
                        'name': 'feature_generator',
                        'strict': True,
                        'schema': schema
                    }
                }
            )
            step_end_ts = time.time()

            raw_generation = response.output_text
            match = re.search(r"\{.*\}", raw_generation, re.DOTALL)
            if not match:
                raise ValueError(f'No JSON found in model output: {raw_generation}')
            logger.debug(f'{log_prefix}: raw generation: {raw_generation}')
            logger.debug(f'{log_prefix}: match: {match.group(0)}')
            generation = json.loads(match.group(0))

            logger.info(f'{log_prefix}: step {step} {generation['metadata']['call_type']} {response.model} ({int(step_end_ts - step_start_ts)}s): {json.dumps(generation)}')
            if generation['metadata']['done']:
                logger.info(f'{log_prefix}: done ({int(step_end_ts - start_ts)}s total)')
                break

            generator_input.append({ 'role': 'assistant', 'content': json.dumps(generation) })
            recommender_input.append({ 'role': 'developer', 'content': f'Feature generation step {step}:\n\n {json.dumps(generation)}' })

            onshape_res = update_partstudio(generation, doc_id, step)
            recommender_input.append({ 'role': 'developer', 'content': f'OnShape response to step {step}:\n\n {json.dumps(onshape_res)}' })

            logger.debug(f'{log_prefix}: generator input: {json.dumps(generator_input)}')
            logger.debug(f'{log_prefix}: recommender input: {json.dumps(recommender_input)}')
        except Exception as e:
            logger.error(f'{log_prefix}: failed: ', exc_info=True)
            break

def recommend_step(step, recommender_input) -> str:
    log_prefix = f'recommend_step()'
    try:
        global openai_client

        start_ts = time.time()
        response = openai_client.responses.create(
            model='gpt-5-nano',
            input=recommender_input,
            reasoning={ 'effort': 'minimal' }
        )
        end_ts = time.time()

        logger.info(f'{log_prefix}: step {step} {response.model} ({int(end_ts - start_ts)}s): {response.output_text}')
        return response.output_text
    except Exception as e:
        logger.error(f'{log_prefix}: failed: ', exc_info=True)
    return ''

def update_partstudio(generation, doc_id, step):
    log_prefix = f'update_partstudio()'
    try:
        call_type = generation['metadata']['call_type']
        onshape_payload = { 'doc_id': doc_id }
        match call_type:
            case 'add_feature':
                if 'featureId' in generation['feature']:
                    del generation['feature']['featureId']
                onshape_payload.update({
                    'feature': generation['feature']
                })
            case 'update_feature':
                onshape_payload.update({
                    'feature': generation['feature'],
                    'feature_id': generation['feature']['featureId']
                })
            case 'delete_feature':
                onshape_payload.update({
                    'feature_id': generation['feature']['featureId']
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
