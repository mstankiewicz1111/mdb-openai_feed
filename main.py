import os
import csv
import io
import requests
import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup
import boto3
from botocore.exceptions import ClientError

# 1. Pobieranie konfiguracji ze zmiennych środowiskowych (ustawimy je na Render)
XML_URL = os.environ.get('IDOSELL_XML_URL')
R2_ENDPOINT_URL = os.environ.get('R2_ENDPOINT_URL')
R2_ACCESS_KEY_ID = os.environ.get('R2_ACCESS_KEY_ID')
R2_SECRET_ACCESS_KEY = os.environ.get('R2_SECRET_ACCESS_KEY')
R2_BUCKET_NAME = os.environ.get('R2_BUCKET_NAME', 'openai-product-feed')
CSV_FILENAME = 'feed.csv'

# Definicja przestrzeni nazw (namespace) z Twojego pliku XML
NS = {'g': 'http://base.google.com/ns/1.0'}

def clean_html(raw_html):
    """Usuwa tagi HTML z opisu i zostawia czysty tekst"""
    if not raw_html:
        return ""
    # Używamy BeautifulSoup do wyciągnięcia samego tekstu
    soup = BeautifulSoup(raw_html, "html.parser")
    # Zamieniamy tagi na spacje, by słowa się nie sklejały, i usuwamy białe znaki z brzegów
    text = soup.get_text(separator=" ", strip=True)
    # Usuwamy ewentualne wielokrotne spacje
    return " ".join(text.split())

def fetch_and_parse_xml():
    """Pobiera XML z IdoSell i wyciąga potrzebne dane"""
    print("Pobieranie pliku XML z IdoSell...")
    response = requests.get(XML_URL)
    response.raise_for_status()
    
    print("Parsowanie pliku XML...")
    root = ET.fromstring(response.content)
    
    products = []
    # Szukamy wszystkich produktów (tag <item> wewnątrz <channel>)
    for item in root.findall('./channel/item'):
        # Pomocnicza funkcja do bezpiecznego wyciągania tekstu
        def get_g_text(tag_name):
            node = item.find(f'g:{tag_name}', NS)
            return node.text if node is not None else ""

        # Wyciąganie i czyszczenie opisu
        raw_description = get_g_text('description')
        clean_description = clean_html(raw_description)

        # Mapowanie dostępności na format OpenAI
        availability = get_g_text('availability')
        if availability == 'in stock':
            availability = 'in_stock'
        elif availability == 'out of stock':
            availability = 'out_of_stock'

        product = {
            'id': get_g_text('id'),
            'title': get_g_text('title'),
            'description': clean_description,
            'link': get_g_text('link'),
            'image_link': get_g_text('image_link'),
            'price': get_g_text('price'),
            'availability': availability,
            'condition': get_g_text('condition'),
            'brand': get_g_text('brand'),
            # Dodatkowe flagi dla OpenAI (opcjonalne, ale zalecane)
            'is_eligible_search': 'true'
        }
        products.append(product)
        
    return products

def generate_csv_and_upload(products):
    """Generuje plik CSV w pamięci RAM i wysyła na Cloudflare R2"""
    print(f"Generowanie pliku CSV dla {len(products)} produktów...")
    
    # Tworzymy plik w pamięci (zamiast na dysku)
    csv_buffer = io.StringIO()
    
    # Definiujemy nagłówki (kolumny) dla OpenAI
    fieldnames = ['id', 'title', 'description', 'link', 'image_link', 'price', 'availability', 'condition', 'brand', 'is_eligible_search']
    
    writer = csv.DictWriter(csv_buffer, fieldnames=fieldnames, quoting=csv.QUOTE_MINIMAL)
    writer.writeheader()
    for product in products:
        writer.writerow(product)
        
    # Przygotowanie klienta Cloudflare R2 (kompatybilnego z S3)
    print("Łączenie z Cloudflare R2...")
    s3_client = boto3.client(
        's3',
        endpoint_url=R2_ENDPOINT_URL,
        aws_access_key_id=R2_ACCESS_KEY_ID,
        aws_secret_access_key=R2_SECRET_ACCESS_KEY,
        region_name='auto' # Wymagane przez bibliotekę boto3
    )
    
    print(f"Wysyłanie pliku {CSV_FILENAME} na Cloudflare...")
    try:
        # Kodowanie stringa CSV do bajtów (UTF-8)
        csv_bytes = csv_buffer.getvalue().encode('utf-8')
        
        s3_client.put_object(
            Bucket=R2_BUCKET_NAME,
            Key=CSV_FILENAME,
            Body=csv_bytes,
            ContentType='text/csv'
        )
        print("✅ Sukces! Plik CSV został zaktualizowany na Cloudflare.")
    except ClientError as e:
        print(f"❌ Błąd podczas wysyłania na chmurę: {e}")

if __name__ == "__main__":
    try:
        products_data = fetch_and_parse_xml()
        if products_data:
            generate_csv_and_upload(products_data)
        else:
            print("Uwaga: Nie znaleziono żadnych produktów w XML.")
    except Exception as e:
        print(f"Wystąpił błąd krytyczny: {e}")
