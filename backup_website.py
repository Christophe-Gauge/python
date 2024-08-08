#!/usr/bin/python3
# -*- coding: utf-8 -*-

import os
import dotenv
import boto3
import urllib
import requests
from bs4 import BeautifulSoup
import mimetypes

'''
See https://technotes.videre.us/en/python/backup-a-website-to-s3/ for documentation.
'''

# Load .env file
dotenv.read_dotenv()
backup_region_name = os.environ.get("backup_region_name", "")
backup_aws_access_key_id = os.environ.get("backup_aws_access_key_id", "")
backup_aws_secret_access_key = os.environ.get("backup_aws_secret_access_key", "")

html_mime_type = 'text/html; charset=utf-8'
bucket_name = 'www.example.com'
extra_files = ['/', '/robots.txt', '/sitemap.xml', 'favicon.ico']

s3 = boto3.resource(
    's3',
    region_name=backup_region_name,
    aws_access_key_id=backup_aws_access_key_id,
    aws_secret_access_key=backup_aws_secret_access_key,
)


def SaveFile(file_name, file_content, mime_type, lang):
    global bucket_name
    my_url = urllib.parse.unquote(file_name, encoding='utf-8', errors='replace')
    my_path = urllib.parse.urlparse(my_url).path
    if my_path == '/':
        my_path = 'index.html'
    if my_path.startswith('/'):
        my_path = my_path[1:]
    print(f'\033[1;32;1m{file_name} -> {my_path} {lang}')
    bucket = s3.Bucket(bucket_name)
    if lang is not None:
        bucket.put_object(Key= my_path, Body=file_content, ContentType=mime_type, StorageClass='REDUCED_REDUNDANCY', CacheControl='max-age=0', ContentLanguage=lang)
    else:
        bucket.put_object(Key= my_path, Body=file_content, ContentType=mime_type, StorageClass='REDUCED_REDUNDANCY', CacheControl='max-age=0')


def get_sitemap(url):
    global extra_files
    full_url = f"https://{url}/sitemap.xml"
    with requests.Session() as req:
        r = req.get(full_url)
        soup = BeautifulSoup(r.content, 'lxml')
        links = [item.text for item in soup.select("loc")]
        for link in links:
            r = req.get(link)
            # print(link, r.status_code)
            if r.status_code == 200:
                html_content = r.content
            else:
                print(f'\033[1;31;1m{link} {r.status_code}')
                continue
            soup = BeautifulSoup(r.content, 'html.parser')
            SaveFile(link, r.content, html_mime_type, soup.html["lang"])

            # Get all CSS links
            for css in soup.findAll("link", rel="stylesheet"):
                if css['href'] not in extra_files:
                    print('\033[1;37;1m', "Found the URL:", css['href'])
                    extra_files.append(css['href'])
    return


def get_others(url):
    global extra_files
    with requests.Session() as req:
        for file in extra_files:
            my_url = requests.compat.urljoin(f"https://{url}", file)

            # Get MIME type using guess_type
            mime_type, encoding = mimetypes.guess_type(my_url)
            if mime_type is None:
                mime_type = html_mime_type
            print("\033[1;37;1mMIME Type:", mime_type)

            r = req.get(my_url)
            if r.status_code == 200:
                if mime_type == html_mime_type:
                    soup = BeautifulSoup(r.content, 'html.parser')
                    lang = soup.html["lang"]
                else:
                    lang = None
                SaveFile(my_url, r.content, mime_type, lang)
                # quit()
                # print(html_content)
            else:
                print(f'\033[1;31;1m{my_url} {r.status_code}')
                continue
    return


if __name__ == '__main__':
    get_sitemap(bucket_name)
    get_others(bucket_name)
