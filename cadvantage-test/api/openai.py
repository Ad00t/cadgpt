import os
import re
import json
from openai import OpenAI

openai = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))
instructions = '' 

def init():
    global instructions
    print('Authenticated with OpenAI\n')
    with open('instructions.txt', 'r', encoding='utf-8') as instructions_file:
        instructions = instructions_file.read()
    print('Instructions:', instructions) 

def generate_actions(current_features, user_input):
    response = openai.responses.create(
        model='gpt-4.1',
        instructions=instructions + f'\n Use this list of currently existing features to inform your actions:\n{current_features.json()}',
        input=user_input
    )
    print('CADGPT:', response.output_text, '\n')
    if response.status != 'completed':
        return [] 
    return json.loads(response.output_text) 
