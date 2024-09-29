#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from flask import Flask, request, jsonify
import os
import logging
import json
import ssl
from OpenSSL import crypto
# pip3 install pyOpenSSL

"""
A sample Flask application with an auto-generated self-signed certificate
that will log POST requests to /webhook JSON payload to a file.
"""

app = Flask(__name__)
context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)

logFile = os.path.realpath(__file__).split('.')[0] + ".log"
logging.basicConfig(filename=logFile, format='%(asctime)s %(levelname)-5s %(message)s', level=logging.DEBUG)
console = logging.StreamHandler()
console.setLevel(logging.INFO)
formatter = logging.Formatter('%(levelname)s  - %(message)s')
console.setFormatter(formatter)
logging.getLogger().addHandler(console)


def cert_gen(
    serialNumber=0,
    validityStartInSeconds=0,
    validityEndInSeconds=10*365*24*60*60,
    KEY_FILE = "private.key",
    CERT_FILE="selfsigned.crt"):
    global context
    # create a key pair
    k = crypto.PKey()
    k.generate_key(crypto.TYPE_RSA, 4096)
    # create a self-signed cert
    cert = crypto.X509()
    cert.get_subject().C = "countryName"
    cert.get_subject().ST = "stateOrProvinceName"
    cert.get_subject().L = "localityName"
    cert.get_subject().O = "organizationName"
    cert.get_subject().OU = "organizationUnitName"
    cert.get_subject().CN = os.uname().nodename
    cert.get_subject().emailAddress = "emailAddress"
    cert.set_serial_number(serialNumber)
    cert.gmtime_adj_notBefore(validityStartInSeconds)
    cert.gmtime_adj_notAfter(validityEndInSeconds)
    cert.set_issuer(cert.get_subject())
    cert.set_pubkey(k)
    cert.sign(k, 'sha512')
    with open(CERT_FILE, "wt") as f:
        f.write(crypto.dump_certificate(crypto.FILETYPE_PEM, cert).decode("utf-8"))
    with open(KEY_FILE, "wt") as f:
        f.write(crypto.dump_privatekey(crypto.FILETYPE_PEM, k).decode("utf-8"))
    context.load_cert_chain(CERT_FILE, KEY_FILE)


@app.route('/webhook', methods=['POST'])
def webhook_receiver():
    try:
        logging.info(request)
        data = json.loads(request.data)
        # data = request.json # Get the JSON data from the incoming request
        # Process the data and perform actions based on the event
        with open ("demofile.txt", "a") as myfile:
            myfile.write(f"{data}\n")
        logging.info("Received webhook data:", data) 
        return jsonify({'message': 'Webhook received successfully'}), 200
    except Exception as e:
        logging.error(f"An error occurred: {e}")
        return jsonify({'message': 'Webhook failed'}), 500 

if __name__ == '__main__':
    cert_gen()
    app.run(host='0.0.0.0', debug=False, ssl_context=context)
        

# curl -X POST -H "Content-Type: application/json" -d '{"message": "Buttery"}' -k https://127.0.0.1:5000/webhook

