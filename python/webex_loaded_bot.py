import requests
import configparser
import json
import logging
from flask import Flask, request

app = Flask(__name__)

# Load config
config = configparser.ConfigParser()
config.read('config.ini')

WEBEX_TOKEN = config['WEBEX']['access_token']
ALLOWED_ROOM_ID = config['WEBEX'].get('allowed_room_id', '').strip()
API_BASE = config['TWS_API']['base_url']
API_USER = config['TWS_API']['user']
API_PASS = config['TWS_API']['password']
VERIFY_SSL = config['TWS_API'].getboolean('verify_ssl', fallback=True)

logging.basicConfig(level=logging.INFO)

def send_webex_message(room_id, text):
    url = "https://webexapis.com/v1/messages"
    headers = {
        "Authorization": f"Bearer {WEBEX_TOKEN}",
        "Content-Type": "application/json"
    }
    data = {"roomId": room_id, "text": text}
    requests.post(url, headers=headers, json=data)

def query_job(job_name):
    url = f"{API_BASE}/plan/current/job/query"
    payload = {
        "filters": {
            "jobInPlanFilter": {
                "jobName": job_name
            }
        }
    }
    headers = {'How-Many': '500', 'Accept': 'application/json'}
    resp = requests.post(
        url,
        auth=(API_USER, API_PASS),
        headers=headers,
        json=payload,
        verify=VERIFY_SSL
    )
    resp.raise_for_status()
    return resp.json()

@app.route('/lab/pcs/maestro/events/webex', methods=['POST'])
def webex_webhook():
    data = request.json
    logging.info(f"Received webhook data: {data}")
    if 'data' not in data or 'id' not in data['data']:
        logging.warning("Malformed webhook payload")
        return '', 400

    # Get message details
    msg_id = data['data']['id']
    msg_url = f"https://webexapis.com/v1/messages/{msg_id}"
    headers = {"Authorization": f"Bearer {WEBEX_TOKEN}"}
    msg_resp = requests.get(msg_url, headers=headers)
    msg = msg_resp.json()
    text = msg.get('text', '')
    room_id = msg.get('roomId')
    person_id = msg.get('personId')

    logging.info(f"Message received: text='{text}', room_id='{room_id}', person_id='{person_id}'")

    # Ignore messages sent by the bot itself
    me_resp = requests.get("https://webexapis.com/v1/people/me", headers=headers)
    bot_id = me_resp.json().get("id")
    if person_id == bot_id:
        logging.info("Ignoring message from self.")
        return '', 200

    if text.startswith('!loaded '):
        job_name = text[len('!loaded '):].strip()
        try:
            jobs = query_job(job_name)
            logging.info(f"Job query for '{job_name}' returned: {jobs}")
            if not jobs:
                send_webex_message(room_id, f"No jobs found for '{job_name}'.")
                logging.info(f"No jobs found for '{job_name}'.")
            else:
                lines = []
                for js in jobs:
                    try:
                        line = (
                            js["jobDefinition"]["jobDefinitionInPlanKey"]["workstationInPlanKey"]["name"]
                            + '#' + js["jobStreamInPlan"]["name"]
                            + '.' + js["name"]
                        )
                        lines.append(line)
                    except Exception as ex:
                        logging.warning(f"Error parsing job entry: {ex}")
                        continue
                if lines:
                    result = "Jobs loaded:\n" + "\n".join(lines)
                    send_webex_message(room_id, result)
                    logging.info(f"Sent job list to room: {result}")
                else:
                    send_webex_message(room_id, f"No jobs found for '{job_name}'.")
                    logging.info(f"No jobs found for '{job_name}' after parsing.")
        except Exception as e:
            send_webex_message(room_id, f"Error querying job: {e}")
            logging.error(f"Error querying job: {e}")

    return '', 200

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=5000)