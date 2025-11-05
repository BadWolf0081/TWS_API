import requests
import configparser
import json
import logging
from flask import Flask, request
from datetime import datetime, timedelta, timezone

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
TIMEZONE_OFFSET = config['TWS_API'].getint('timezone_offset', fallback=0)

logging.basicConfig(level=logging.INFO)

def send_webex_card(room_id, card_json):
    url = "https://webexapis.com/v1/messages"
    headers = {
        "Authorization": f"Bearer {WEBEX_TOKEN}",
        "Content-Type": "application/json"
    }
    data = {"roomId": room_id, "attachments": [{"contentType": "application/vnd.microsoft.card.adaptive", "content": card_json}]}
    resp = requests.post(url, headers=headers, json=data)
    logging.info(f"Webex send card response: {resp.status_code} {resp.text}")
    return resp

def send_webex_message(room_id, text):
    url = "https://webexapis.com/v1/messages"
    headers = {
        "Authorization": f"Bearer {WEBEX_TOKEN}",
        "Content-Type": "application/json"
    }
    data = {"roomId": room_id, "text": text}
    resp = requests.post(url, headers=headers, json=data)
    logging.info(f"Webex send message response: {resp.status_code} {resp.text}")
    return resp

def create_menu_card():
    return {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.2",
        "body": [
            {
                "type": "TextBlock",
                "text": "Maestro Job Query",
                "weight": "Bolder",
                "size": "Medium"
            },
            {
                "type": "TextBlock",
                "text": "Select an action and provide the job name:",
                "wrap": True
            },
            {
                "type": "Input.ChoiceSet",
                "id": "action",
                "style": "compact",
                "placeholder": "Choose an action",
                "choices": [
                    {
                        "title": "Is my job loaded?",
                        "value": "loaded"
                    },
                    {
                        "title": "Will my job run in today's plan?",
                        "value": "willrun"
                    }
                ]
            },
            {
                "type": "Input.Text",
                "id": "jobname",
                "placeholder": "Enter job/jobstream name",
                "maxLength": 100
            },
            {
                "type": "Input.Text",
                "id": "enddate",
                "placeholder": "End date (YYYY-MM-DD) - only for 'Will run' option",
                "maxLength": 10,
                "isVisible": True
            }
        ],
        "actions": [
            {
                "type": "Action.Submit",
                "title": "Submit",
                "data": {
                    "action": "submit_query"
                }
            }
        ]
    }

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

def query_jobstreams(js_name):
    url = f"{API_BASE}/model/jobstream"
    params = {'key': js_name}
    headers = {'Accept': 'application/json'}
    resp = requests.get(
        url,
        auth=(API_USER, API_PASS),
        headers=headers,
        params=params,
        verify=VERIFY_SSL
    )
    resp.raise_for_status()
    return resp.json()

def rc_evaluation(jobstream_id, from_date, to_date):
    url = f"{API_BASE}/model/jobstream/{jobstream_id}/rc-evaluation"
    params = {'from': from_date, 'to': to_date}
    headers = {'Accept': 'application/json'}
    resp = requests.get(
        url,
        auth=(API_USER, API_PASS),
        headers=headers,
        params=params,
        verify=VERIFY_SSL
    )
    resp.raise_for_status()
    return resp.json()

def format_start_time(utc_str, offset_hours):
    dt = datetime.strptime(utc_str, "%Y-%m-%dT%H:%M:%S.%fZ")
    dt_local = dt + timedelta(hours=offset_hours)
    return dt_local.strftime("%H:%M on %Y-%m-%d")

@app.route('/lab/pcs/maestro/events/webex', methods=['POST'])
def webex_webhook():
    data = request.json
    logging.info(f"Received webhook data: {data}")
    
    # Handle card submissions
    if 'data' in data and 'inputs' in data['data']:
        return handle_card_submission(data)
    
    # Handle regular messages
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

    # Only respond in the allowed room
    if ALLOWED_ROOM_ID and room_id != ALLOWED_ROOM_ID:
        logging.info(f"Ignoring message from room {room_id} (not allowed).")
        return '', 200

    # Remove "Maestro" mention from the beginning if present
    if text.lower().startswith("maestro"):
        text = text[len("maestro"):].lstrip(" :").lstrip()

    logging.info(f"Message received: text='{text}', room_id='{room_id}', person_id='{person_id}'")

    # Ignore messages sent by the bot itself
    me_resp = requests.get("https://webexapis.com/v1/people/me", headers=headers)
    bot_id = me_resp.json().get("id")
    if person_id == bot_id:
        logging.info("Ignoring message from self.")
        return '', 200

    # If message starts with !, show the menu card
    if text.startswith('!'):
        card = create_menu_card()
        send_webex_card(room_id, card)
        logging.info("Sent menu card to room")

    return '', 200

def handle_card_submission(data):
    inputs = data['data']['inputs']
    room_id = data['data']['roomId']
    
    action = inputs.get('action', '')
    jobname = inputs.get('jobname', '').strip()
    enddate = inputs.get('enddate', '').strip()
    
    logging.info(f"Card submission: action={action}, jobname={jobname}, enddate={enddate}")
    
    if not jobname:
        send_webex_message(room_id, "Please provide a job name.")
        return '', 200
    
    if action == 'loaded':
        handle_loaded_query(room_id, jobname)
    elif action == 'willrun':
        if not enddate:
            enddate = datetime.utcnow().strftime('%Y-%m-%d')
        handle_willrun_query(room_id, jobname, enddate)
    else:
        send_webex_message(room_id, "Please select an action from the dropdown.")
    
    return '', 200

def handle_loaded_query(room_id, job_name):
    try:
        jobs = query_job(job_name)
        logging.info(f"Job query for '{job_name}' returned: {jobs}")
        if not jobs:
            send_webex_message(room_id, f"No jobs found for '{job_name}'.")
        else:
            lines = []
            for js in jobs:
                try:
                    line = (
                        js["jobDefinition"]["jobDefinitionInPlanKey"]["workstationInPlanKey"]["name"]
                        + '#' + '\u200b' + js["jobStreamInPlan"]["name"]
                        + '.' + js["name"]
                        + '   State: ' + js["status"]["internalStatus"]
                        + '   Start Time: ' + format_start_time(js["jobStreamInPlan"]["startTime"], TIMEZONE_OFFSET)
                    )
                    lines.append(line)
                except Exception as ex:
                    logging.warning(f"Error parsing job entry: {ex}")
                    continue
            if lines:
                result = "Jobs loaded:\n" + "\n".join(lines)
                send_webex_message(room_id, result)
            else:
                send_webex_message(room_id, f"No jobs found for '{job_name}'.")
    except Exception as e:
        send_webex_message(room_id, f"Error querying job: {e}")
        logging.error(f"Error querying job: {e}")

def handle_willrun_query(room_id, js_name, to_date):
    today_str = datetime.utcnow().strftime('%Y-%m-%d')
    try:
        jobstreams = query_jobstreams(js_name)
        logging.info(f"Jobstream query for '{js_name}' returned: {jobstreams}")
        if not jobstreams:
            send_webex_message(room_id, f"No job streams found for '{js_name}'.")
        else:
            if isinstance(jobstreams, dict):
                jobstreams = [jobstreams]
            lines = []
            for js in jobstreams:
                try:
                    js_id = js["header"]["id"]
                    rc_eval = rc_evaluation(js_id, today_str, to_date)
                    selected_dates = [
                        entry["date"]
                        for entry in rc_eval.get("results", [])
                        if "SELECTED" in entry.get("type", [])
                    ]
                    if selected_dates:
                        line = f"Job Stream ID: {js_id}\nSelected Dates:\n" + "\n".join(selected_dates)
                    else:
                        line = f"Job Stream ID: {js_id}\nNo SELECTED dates found."
                    lines.append(line)
                    logging.info(f"Job Stream ID: {js_id} SELECTED dates: {selected_dates}")
                except Exception as ex:
                    logging.warning(f"Error processing job stream entry: {ex}")
                    continue
            if lines:
                result = "Job Streams RC Evaluation:\n\n" + "\n\n".join(lines)
                send_webex_message(room_id, result)
            else:
                send_webex_message(room_id, f"No job streams found for '{js_name}' after parsing.")
    except Exception as e:
        send_webex_message(room_id, f"Error querying job stream: {e}")
        logging.error(f"Error querying job stream: {e}")

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=80)