#!/usr/bin/env python
# -*- coding: utf-8 -*-
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)

# GNU Lesser General Public License v3.0 or later
#
# Copyright (C) 2025 Christophe Gauge
# https://github.com/Christophe-Gauge/python/blob/main/sidecar_monitor.py
# https://technotes.videre.us/en/python/building-a-python-sidecar-to-monitor-server-performance/
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
# Further information and clarification of this license can be found at
# https://www.gnu.org/licenses/lgpl-3.0.html
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.


__author__ = "Christophe Gauge"
__version__ = "1.0.7"


'''
Sidecar script for Docker host.

For all hosts:
    - Monitor disk space usage
    - Monitor network interface packet drop counter
    - Monitor total Virtual Memory usage
    - Monitor total Resident Memory usage
    - Monitor processes using large amounts of Memory
    - Report issues in Slack channel #cloud-ops (max 30 per day)
    - Log the memory used by this script (DEBUG logging only)
    - Monitor that the required Docker containers are running
    - Monitor a given log file for errors

'''


# I M P O R T S ###############################################################


import os
import io
import sys
import logging
from logging.handlers import RotatingFileHandler
import docker
import time
import psutil
import schedule
from threading import Thread
import signal
import socket
import boto3
import json
import urllib3
import traceback
import datetime


# G L O B A L S ###############################################################

log_level = logging.INFO
logFile = os.path.realpath(__file__).split('.')[0] + ".log"

sts_client = boto3.client('ssm', region_name="us-west-2")
parameter = sts_client.get_parameter(Name="/application/slackurl", WithDecryption=True)
server_url = parameter['Parameter']['Value']
print(server_url)
parameter = sts_client.get_parameter(Name="/application/slackcloudoperations", WithDecryption=True)
slack_url_cloudoperations = server_url + parameter['Parameter']['Value']
http = urllib3.PoolManager()

service_tier = "PROD"
run = True
disk_used_percent_threshold = 95
cpu_percent_used_threshold = 900
swap_percent_threshold = 80
mem_used_percent_threshold = 95
cpu_percent_threshold = 98.0
cpu_iowait_threshold = 1000
net_error_threshold = 20000
process_memory_threshold = 2 * 1024 * 1024 * 1024  # 2 GB

required_containers = ['container-1', 'container-2']
log_file_to_monitor = "/home/user1/job_output/run.log"
log_file_last_line = 0
log_file_number_of_reads = 0

MAX_LOG_LINES = 50
MAX_LOG_CHARS = int(4000 * .98)  # slack messages can have at most 4000 chars

max_slack_notifications_per_day = 30
slack_docker_notifications_count = 0
slack_system_notifications_count = 0

server_name = socket.gethostname().split('.')[0].lower()
slack_title = "Docker Sidecar - " + server_name
slack_user_name = "CLOUD-DEVOPS"
slack_icon_emoji = ":registeel:"
docker_event_map = {
    'start': {'proper_name': 'started', 'color': '36a64f'},
    'die': {'proper_name': 'stopped', 'color': 'FFD700'}
}

logger = logging.getLogger()
logger.setLevel(log_level)

formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

# Console logging
ch = logging.StreamHandler()
ch.setLevel(log_level)
ch.setFormatter(formatter)
logger.addHandler(ch)
logger.info("-" * 80)

# Log to file
try:
    fh = RotatingFileHandler(logFile, maxBytes=(1048576 * 50), backupCount=7)
    fh.setLevel(log_level)
    fh.setFormatter(formatter)
    logger.addHandler(fh)
    logger.info("Log file:    %s" % logFile)
except Exception as e:
    logger.warning("Unable to log to file: %s - %s" % (logFile, e))

logger.info("Path:    %s" % (os.path.realpath(__file__)))
logger.info("Version:  %s" % (__version__))


# C O D E #####################################################################


def total_seconds(dt):
    """Keep backward compatibility with Python 2.6 which doesn't have this method."""
    if hasattr(datetime, 'total_seconds'):
        return dt.total_seconds()
    else:
        return (dt.microseconds + (dt.seconds + dt.days * 24 * 3600) * 10**6) / 10**6


def time_this(original_function):
    def new_function(*args, **kwargs):
        before = datetime.datetime.now()
        x = original_function(*args, **kwargs)
        after = datetime.datetime.now()
        global logger
        logger.info('Function %s took %.4fs' % (original_function.__name__, total_seconds(after - before)))
        return x
    return new_function


def GetHumanReadable(size, precision=2):
    """Transform file size to human readable number."""
    suffixes = ['B', 'KB', 'MB', 'GB', 'TB']
    suffixIndex = 0
    while size > 1024 and suffixIndex < 4:
        suffixIndex += 1  # increment the index of the suffix
        size = size / 1024.0  # apply the division
    return "%.*f%s" % (precision, size, suffixes[suffixIndex])


def run_threaded(job_func, args=''):
    """Run a scheduled job as a thread."""
    if args == '':
        job_thread = Thread(target=job_func)
    else:
        job_thread = Thread(target=job_func, args=(args,))
    job_thread.start()


def handler_stop_signals(signum, frame):
    """Handles the SIGTERM signal to stop script cleanly."""
    logger.info("Received %s signal, exiting." % signum)
    global run
    run = False
    sys.exit(0)


def sendSlack(myText, myColor="#ff0000"):
    """Create a new thread and call the send Slack function."""
    global slack_docker_notifications_count
    global slack_system_notifications_count
    global max_slack_notifications_per_day
    global slack_channel

    # If this is an event from watch_and_notify_events
    if myText.lower().startswith('container'):
        # Not sending Docker events for DEV systems
        if service_tier.upper() == "DEV":
            logger.warning("Docker event on DEV system, NOT sending!")
            return
        # Not sending if the max count has been exceeded
        if slack_docker_notifications_count > max_slack_notifications_per_day:
            logger.warning("Slack Docker Notification max count exceeded for today, NOT sending!")
            return
        else:
            # Adding a mention that the max has been reached
            if slack_docker_notifications_count == max_slack_notifications_per_day:
                myText += '\n*Max Docker notification reached!*'
            slack_docker_notifications_count += 1
    # Else if this is a system event
    else:
        # Not sending if the max count has been exceeded
        if slack_system_notifications_count > max_slack_notifications_per_day:
            logger.warning("Slack System Notification max count exceeded for today, NOT sending!")
            return
        else:
            # Adding a mention that the max has been reached
            if slack_system_notifications_count == max_slack_notifications_per_day:
                myText += '\n*Max System notification reached!*'
            slack_system_notifications_count += 1

    logger.info('Slack - %s - %s - %s' % (slack_system_notifications_count, slack_docker_notifications_count, myText))

    worker = Thread(target=sendSlackThread(myText, myColor, service_tier,))
    worker.daemon = True
    worker.start()


def sendSlackThread(message, myColor, service_tier):
    """Send a message to Slack and handle rate limiting."""
    global slack_url_cloudoperations
    global slack_title
    global slack_user_name
    global slack_icon_emoji
    if service_tier.upper() == 'PROD':
        new_title = '<!channel|channel> ' + slack_title
    else:
        new_title = slack_title

    slack_message = {
        "text": new_title,
        "attachments": [
            {
                "fallback": message,
                "color": myColor,
                "text": message
            }
        ]
    }
    # Post message on SLACK_WEBHOOK_URL
    encoded_msg = json.dumps(slack_message).encode('utf-8')
    try:
        resp = http.request('POST',slack_url_cloudoperations, body=encoded_msg)
        print(f"Message sent {resp}")
    except Exception as e:
        logger.error("Error {0}".format(str(e)))
        logger.error(traceback.format_exc())


def watch_and_notify_events(client):
    """Watch for Docker events and send Slack messages."""
    # event_filters = {"event": "die"}
    event_filters = {"type": "container", "event": ["create", "start", "die", "destroy"]}

    for event in client.events(filters=event_filters, decode=True):
        try:
            # logger.info(event)
            logger.info(
                'Docker event: ' + event['status'] +
                ' Name: ' + event['Actor']['Attributes']['name'] +
                ' Image: ' + event['from'] +
                ' ID: ' + event['id']
            )
            if event['status'] in ['start', 'die']:
                sendSlack('Container: `' + event['Actor']['Attributes']['name'] + '` *' + docker_event_map[event['status']]['proper_name'] + '*\nImage: `' + event['from'] + '`', docker_event_map[event['status']]['color'])
        except Exception as e:
            logger.error('ERROR: %s' % e)
            logger.error(traceback.format_exc())

        if event['status'] in ['create', 'start']:
            try:
                target = client.containers.get(event['id'])
                if event['status'] == 'create':
                    # logger.info(target)
                    logger.info(event)
                if event['status'] == 'start':
                    logger.info('Docker ports: ' + str(target.ports))
                    time.sleep(5)
                    logs = target.logs(tail=MAX_LOG_LINES)
                    log_msg = "*Last log* entries are:\n\n```\n{}\n```".format(
                        logs.decode('utf8')[-MAX_LOG_CHARS:]
                    )
                    logger.info(log_msg)
            except Exception as e:
                logger.warning('ERROR: %s' % e)
                logger.warning(traceback.format_exc())


@time_this
def Check_Required_Containers():
    """Alerts if disk usage is above threshold."""
    global client
    global required_containers
    temp_container_list = list(required_containers)
    try:
        for container in client.containers.list():
            logger.debug('Running containers: ' + container.id + ' ' + container.name)
            if container.name in temp_container_list and container.status == 'running':
                temp_container_list.remove(container.name)
    except Exception as e:
        logger.warning('ERROR: %s' % e)
        logger.warning(traceback.format_exc())
    if len(temp_container_list) > 0:
        alertText = "Docker container not running: %s" % (' '.join(temp_container_list))
        logger.warning(alertText)
        sendSlack(alertText)


@time_this
def Check_Disk_Usage():
    """Alerts if disk usage is above threshold."""
    logger.info('Disk Partitions:')
    for entry in psutil.disk_partitions(all=True):
        if entry.device.startswith('/dev') or entry.device.startswith('ebis'):
            if 'snapshot' not in entry.device:
                try:
                    disk_usage = psutil.disk_usage(entry.mountpoint).percent
                    logger.info("Disk - Device: %s    Mount point: %s    File system type: %s    is %.0f%% used" % (entry.device, entry.mountpoint, entry.fstype, disk_usage))
                    if disk_usage > disk_used_percent_threshold:
                        alertText = "High Disk usage on %s: %.0f%% used" % (entry.mountpoint, disk_usage)
                        logger.warning(alertText)
                        sendSlack(alertText)
                except Exception as e:
                    logger.warning("Error {0}".format(str(e)))
                    logger.warning(traceback.format_exc())


@time_this
def Check_Memory_Usage():
    """Alerts if Resident or Virtual memory usage are above threshold."""
    count = 0
    for p in psutil.process_iter():
        count += 1
        try:
            if p.memory_info().rss > process_memory_threshold:
                logger.info("Process - %s PID %d memory used: %s RSS(Resident Set Size) %s VMS(Virtual Memory Size)" % (p.name(), p.pid, GetHumanReadable(p.memory_info().rss), GetHumanReadable(p.memory_info().vms)))
                if p.name() in ['java', 'python', 'python3']:
                    logger.info("Process - %s PID %d parameters: %s" % (p.name(), p.pid, p.exe() + ' '.join(p.cmdline())))
        except psutil.AccessDenied:
            continue
        except psutil.NoSuchProcess:
            continue
        except Exception as e:
            logger.error("Error {0}".format(str(e)))
            logger.error(traceback.format_exc())

    try:
        # memory usage:
        mem = psutil.virtual_memory()
        if mem.percent > mem_used_percent_threshold:
            alertText = "High Memory usage: %.0f%%" % mem.percent
            logger.warning(alertText)
            logger.warning("Memory - Total:     %10s" % (GetHumanReadable((mem.total))))
            logger.warning("Memory - Available: %10s" % (GetHumanReadable((mem.available))))
            logger.warning("Memory - used Percent: %.0f%% " % (mem.percent))
            logger.warning("Memory - used:      %10s" % (GetHumanReadable((mem.used))))
            logger.warning("Memory - free:      %10s" % (GetHumanReadable((mem.free))))
            logger.warning("Memory - active:    %10s" % (GetHumanReadable((mem.active))))
            logger.warning("Memory - inactive:  %10s" % (GetHumanReadable((mem.inactive))))
            logger.warning("Memory - buffers:   %10s" % (GetHumanReadable((mem.buffers))))
            logger.warning("Memory - cached:    %10s" % (GetHumanReadable((mem.cached))))
            sendSlack(alertText)
    except Exception as e:
        logger.error("Error {0}".format(str(e)))
        logger.error(traceback.format_exc())

    try:
        # swap usage
        swap = psutil.swap_memory()
        if swap.percent > swap_percent_threshold:
            alertText = "High swap usage: %.0f%%" % swap.percent
            logger.warning(alertText)
            logger.warning("Swap - Total:       %10s" % (GetHumanReadable((swap.total))))
            logger.warning("Swap - Used:        %10s" % (GetHumanReadable((swap.used))))
            logger.warning("Swap - Free:        %10s" % (GetHumanReadable((swap.free))))
            logger.warning("Swap - used Percent:    %.0f%% " % (swap.percent))
            logger.warning("Swap - sin:         %10s" % (GetHumanReadable((swap.sin))))
            logger.warning("Swap - sout:        %10s" % (GetHumanReadable((swap.sout))))
            sendSlack(alertText)
    except Exception as e:
        logger.error("Error {0}".format(str(e)))
        logger.error(traceback.format_exc())

    try:
        perc = psutil.cpu_times_percent(interval=0.5, percpu=False)
        if perc.iowait > cpu_iowait_threshold:
            alertText += "High CPU iowait count\n"
            logger.warning(" High CPU iowait ALL: %s " % perc.iowait)
            sendSlack(alertText)
        '''
            avgCPU = []
            for x in range(10):
                currentCPU = psutil.cpu_percent(interval=10)
                avgCPU.append(currentCPU)
                logger.debug(psutil.cpu_times().iowait)

            logger.debug(float(reduce(lambda x, y: x + y, avgCPU) / len(avgCPU)))
        '''
    except Exception as e:
        logger.error("Error:", e)
        logger.error(traceback.format_exc())


@time_this
def Check_CPU_Usage():
    try:
        perc = psutil.cpu_percent(interval=300, percpu=False)
        if perc > cpu_percent_threshold:
            alertText = "High CPU iowait count\n"
            logger.warning(" High CPU use ALL: %.0f%% " % perc)
            sendSlack(alertText)
    except Exception as e:
        logger.error("Error:", e)
        logger.error(traceback.format_exc())

    try:
        perc = psutil.cpu_times_percent(interval=0.5, percpu=False)
        if perc.iowait > cpu_iowait_threshold:
            alertText = "High CPU iowait count\n"
            logger.warning(" High CPU iowait ALL: %s " % perc.iowait)
            sendSlack(alertText)
            # logger.info(float(reduce(lambda x, y: x + y, avgCPU) / len(avgCPU)))
    except Exception as e:
        logger.error("Error:", e)
        logger.error(traceback.format_exc())


@time_this
def Check_Network_Drops():
    """Alerts if number of dropped packets is above threshold."""
    try:
        if psutil.net_io_counters().errin + psutil.net_io_counters().errout + psutil.net_io_counters().dropin + psutil.net_io_counters().dropout > net_error_threshold:
            alertText = "High number of network drops or errors"
            logger.warning(alertText)
            sendSlack(alertText)

            net = psutil.net_io_counters(pernic=True)
            logger.warning(alertText)
            for nic in net:
                logger.warning("%s: %s" % (nic, str(net[nic])))

    except Exception:
        logger.warning(traceback.format_exc())
        pass


def resetSlackCount():
    """Reset the daily Slack notification count."""
    global slack_docker_notifications_count
    global slack_system_notifications_count
    slack_docker_notifications_count = 0
    slack_system_notifications_count = 0

@time_this
def monitorLogFile():
    """Monitors a log file for new errors"""
    global log_file_to_monitor, log_file_last_line, log_file_number_of_reads
    logger.info(f'Run {log_file_number_of_reads} - Checking file {log_file_to_monitor} offset {log_file_last_line}')
    error_lines = ''
    try:
        with open(log_file_to_monitor, 'r') as file:
            file.seek(log_file_last_line)
            lines = file.readlines()
            if lines:
                number_of_new_lines = 0
                for line in lines:
                    number_of_new_lines += 1
                    # Don't alert if this is the first time we read the file
                    if log_file_number_of_reads > 1:
                        if 'error' in line.lower():
                            logger.info(line.strip())
                            error_lines += line
                logger.info(f"Processed {number_of_new_lines} new lines, new offset is {log_file_last_line}")
            else:
                logger.info("No new lines")
            # Readjust pointer in case the file was rotated and the pointer is too large
            file.seek(io.SEEK_SET, io.SEEK_END)
            log_file_last_line = file.tell()
    except FileNotFoundError:
       logger.error(f"Error: File not found at {log_file_to_monitor}")
    except Exception as e:
         logger.error(f"An error occurred: {e}")
    log_file_number_of_reads += 1
    if error_lines != "":
        alertText = f"Errors in log {log_file_to_monitor}:\n{error_lines}"
        logger.warning(alertText)
        sendSlack(alertText)
      

def main():
    """Main function."""
    global run
    global client

    # Checks if the system was newly rebooted
    bootTime = psutil.boot_time()
    upTime = float(time.time() - bootTime)  # system uptime in seconds
    logger.info("%s server - Uptime: %.5fs" % (service_tier, upTime))
    # If the uptime is less than 5 minutes then let's wait and give services a chance to start
    if upTime < 300:
        logger.info("System recently rebooted, waiting.")
        sendSlack('Server was rebooted.')
        time.sleep(120)

    signal.signal(signal.SIGINT, handler_stop_signals)
    signal.signal(signal.SIGTERM, handler_stop_signals)

    schedule.every(5).to(15).minutes.do(run_threaded, Check_Disk_Usage)
    schedule.every(15).to(45).minutes.do(run_threaded, Check_Memory_Usage)
    schedule.every(10).to(20).minutes.do(run_threaded, Check_Required_Containers)
    # schedule.every(4).to(6).hours.do(run_threaded, Check_Network_Drops)
    schedule.every(1).to(2).hours.do(run_threaded, Check_CPU_Usage)
    schedule.every(24).hours.do(resetSlackCount)
    schedule.every(1).hours.do(monitorLogFile)

    client = docker.DockerClient(base_url='unix://var/run/docker.sock')

    worker = Thread(target=watch_and_notify_events, args=(client,))
    worker.daemon = True
    worker.start()
    logger.info('Thread started!')

    while run:
        schedule.run_pending()
        time.sleep(1)

    sys.exit(0)

###############################################################################


if __name__ == "__main__":
    main()

# E N D   O F   F I L E #######################################################
