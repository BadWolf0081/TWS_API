#!/usr/bin/python
import waconn
import argparse

parser = argparse.ArgumentParser(description='Query job.')
parser.add_argument('-j','--jname', help='job name filter', required=True, metavar="J_FILTER")

args = parser.parse_args()
conn = waconn.WAConn('waconn.ini','/twsd')

# Query to find pools matching provided filter
resp = conn.post('/plan/current/job/query',
	{ "filters": { "jobInPlanFilter": { "jobName": args.jname } } }, 
	headers={'How-Many': '500'})

r = resp.json()

#print json.dumps(r, indent=2)
for js in r:
    print(js["key"]["workstationKey"]["name"]+'#'+js["key"]["name"]+'('+js["key"]["startTime"]+')')

