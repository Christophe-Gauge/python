#!/usr/bin/env python3
# -*- coding: utf-8 -*-

'''
See documentation at: https://technotes.videre.us/en/linux/step-by-step-guide-to-installing-idstower-on-a-raspberry-pi-5/
'''

import socket
import json
import os
import sys
import requests

socket_path = '/tmp/suricata_eve.sock'
HOOK_URL = 'https://hooks.slack.com/services/<YOUR_OWN_URL>'
slack_user = 'IDSTower'
slack_icon = ':warning:'

def sendSlack(title, attachments, severity):
    """Send a given message to Slack."""
    if severity <= 3:
        color = '36a64f'
        alert = ''
    elif severity < 6:
        color = 'FFD700'
        alert = ''
    elif severity < 8:
        color = 'FF8C00'
        alert = '<!channel|channel> '
    elif severity >= 8:
        color = 'FF4500'
        alert = '<!channel|channel> '
    else:
        color = '2F4F4F'
        alert = ''

    headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
    data = {
            "username": slack_user,
            "icon_emoji": slack_icon,
            "text": f'{title}{alert}', #'New device on network <!channel|channel>',
            "attachments": [
                {
                    # "fallback": title,
                    "color": color,
                    "text": f'```{attachments}```'
                }
            ]
        }
    print("Sending message to Slack")
    try:
        r = requests.request(method='POST', url=HOOK_URL, data=json.dumps(data), headers=headers, timeout=25)
    except Exception as x:
        print('Connection failed :( %s' % x.__class__.__name__)
        print('Connection failed :( %s' % x)
        raise Exception('Failed to send to Slack')
    else:
        if r.status_code == 200:
            response = r.content
            print(response)
        else:
            print('Error: %s' % r.status_code)
            print(r.headers)
            print(r.text)
            print(sys.exc_info()[:2])
            raise Exception('Failed to send to Slack')
    finally:
        print('Sent to Slack')


# Ensure the socket file does not exist
try:
    os.unlink(socket_path)
except OSError:
    if os.path.exists(socket_path):
        raise

sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
#sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    #sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) # Use SOCK_STREAM for TCP
    #sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(socket_path)
except socket.error as msg:
    print (msg)
    exit(1)

sock.listen(1)
client_socket, address = sock.accept()
print(f"Listening on socket: {socket_path}")

while True:
    try:
        data = client_socket.recv(7168)
        if not data:
            continue #break  # Connection closed by client
        print(f"Received: {data.decode()}")

        if data:
            for entry in data.decode('utf-8').split('\n'):
                event = json.loads(entry)
                if event['event_type'] == 'alert':
                    alert_data = event['alert']
                    if 'severity' in alert_data:
                        severity = alert_data['severity']
                    else:
                        severity = 0
                    title = f'{alert_data["category"]}\n{alert_data["signature"]}'
                    attachment = json.dumps(event, sort_keys=True, indent=4)
                    sendSlack(title, attachment, severity)
                    print("-" * 20)
    except KeyboardInterrupt:
        print("Exiting...")
        break
    except json.JSONDecodeError:
        print("Invalid JSON data received.")
    except socket.error as e:
        print(f"Socket error: {e}")
    except Exception as e:
            print(f"An error occurred: {e}")

sock.close()
os.unlink(socket_path)
sys.exit(0)
