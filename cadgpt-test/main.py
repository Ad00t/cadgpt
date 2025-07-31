from dotenv import load_dotenv
load_dotenv()

import traceback
from api import onshape, openai 

onshape.init()
openai.init()

while True:
    try:
        user_input = input('User: ')
        print()
        current_features = onshape.get_features() 
        if current_features.status_code != 200:
            continue
        actions = openai.generate_actions(current_features, user_input)
        for action in actions:
            print(action)
            match action['type']:
                case 'add_feature':
                    onshape_response = onshape.add_feature(action['body'])
                case 'update_feature':
                    onshape_response = onshape.update_feature(action['fid'], action['body'])
                case 'delete_feature':
                    onshape_response = onshape.delete_feature(action['fid'])
    except KeyboardInterrupt:
        break
    except Exception as e:
        print(traceback.format_exc())
