import os
import requests
import configparser
import logging
from datetime import datetime, timedelta
from flask import Flask, request, Response
from botbuilder.core import BotFrameworkAdapter, BotFrameworkAdapterSettings, TurnContext
from botbuilder.schema import Activity, ActivityTypes

app = Flask(__name__)

# Load config
config = configparser.ConfigParser()
config.read('config.ini')

MS_APP_ID = os.environ.get("MICROSOFT_APP_ID", "")
MS_APP_PASSWORD = os.environ.get("MICROSOFT_APP_PASSWORD", "")
ALLOWED_CHANNEL_ID = config['TEAMS'].get('allowed_channel_id', '').strip()
API_BASE = config['TWS_API']['base_url']
API_USER = config['TWS_API']['user']
API_PASS = config['TWS_API']['password']
VERIFY_SSL = config['TWS_API'].getboolean('verify_ssl', fallback=True)
TIMEZONE_OFFSET = config['TWS_API'].getint('timezone_offset', fallback=0)

logging.basicConfig(level=logging.INFO)

adapter_settings = BotFrameworkAdapterSettings(MS_APP_ID, MS_APP_PASSWORD)
adapter = BotFrameworkAdapter(adapter_settings)

def send_teams_message(turn_context: TurnContext, text: str):
    return turn_context.send_activity(Activity(type=ActivityTypes.message, text=text))

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

async def on_message_activity(turn_context: TurnContext):
    activity = turn_context.activity
    text = activity.text.strip()
    channel_id = activity.conversation.id
    user_id = activity.from_property.id

    # Only respond in the allowed Teams channel
    if ALLOWED_CHANNEL_ID and channel_id != ALLOWED_CHANNEL_ID:
        logging.info(f"Ignoring message from channel {channel_id} (not allowed).")
        return

    # Remove "Maestro" mention from the beginning if present
    if text.lower().startswith("maestro"):
        text = text[len("maestro"):].lstrip(" :").lstrip()

    logging.info(f"Message received: text='{text}', channel_id='{channel_id}', user_id='{user_id}'")

    if text.startswith('!loaded '):
        job_name = text[len('!loaded '):].strip()
        try:
            jobs = query_job(job_name)
            logging.info(f"Job query for '{job_name}' returned: {jobs}")
            if not jobs:
                await send_teams_message(turn_context, f"No jobs found for '{job_name}'.")
                logging.info(f"No jobs found for '{job_name}'.")
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
                    await send_teams_message(turn_context, result)
                    logging.info(f"Sent job list to channel: {result}")
                else:
                    await send_teams_message(turn_context, f"No jobs found for '{job_name}'.")
                    logging.info(f"No jobs found for '{job_name}' after parsing.")
        except Exception as e:
            await send_teams_message(turn_context, f"Error querying job: {e}")
            logging.error(f"Error querying job: {e}")

    elif text.startswith('!willrun '):
        parts = text[len('!willrun '):].strip().split()
        if len(parts) != 2:
            await send_teams_message(turn_context, "Usage: !willrun JOBSTREAMNAME YYYY-MM-DD")
            return
        js_name, to_date = parts
        today_str = datetime.utcnow().strftime('%Y-%m-%d')
        try:
            jobstreams = query_jobstreams(js_name)
            logging.info(f"Jobstream query for '{js_name}' returned: {jobstreams}")
            if not jobstreams:
                await send_teams_message(turn_context, f"No job streams found for '{js_name}'.")
                logging.info(f"No job streams found for '{js_name}'.")
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
                    await send_teams_message(turn_context, result)
                    logging.info(f"Sent job stream RC evaluation to channel.")
                else:
                    await send_teams_message(turn_context, f"No job streams found for '{js_name}' after parsing.")
                    logging.info(f"No job streams found for '{js_name}' after parsing.")
        except Exception as e:
            await send_teams_message(turn_context, f"Error querying job stream: {e}")
            logging.error(f"Error querying job stream: {e}")

@app.route("/api/messages", methods=["POST"])
def messages():
    if "application/json" in request.headers["Content-Type"]:
        body = request.json
    else:
        return Response(status=415)
    activity = Activity().deserialize(body)
    auth_header = request.headers.get("Authorization", "")
    async def call_bot_adapter():
        await adapter.process_activity(activity, auth_header, on_message_activity)
    import asyncio
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(call_bot_adapter())
    return Response(status=201)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=3978)