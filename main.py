import os
import csv
import io
import requests
import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup
import boto3
from botocore.exceptions import ClientError

# 1. Pobieranie konfiguracji ze zmiennych środowiskowych
XML_URL = os.environ.get('IDOSELL_XML_URL')
R2_ENDPOINT_URL = os.environ.get('R2_ENDPOINT_URL')
R2_ACCESS_KEY_ID = os.environ.get('R2_ACCESS_KEY_ID')
R2_SECRET_ACCESS_KEY = os.environ.get('R2_SECRET_ACCESS_KEY')
R2_BUCKET_NAME = os.environ.get('R2_BUCKET_NAME', 'openai-product-feed')
CSV_FILENAME = 'feed.csv'

# Definicja przestrzeni nazw z Twojego pliku XML
NS = {'g': 'http://base.google.com/ns/1.0'}

def clean_html(raw_html):
    """Usuwa tagi HTML z opisu i zostawia czysty tekst"""
    if not raw_html:
        return ""
    soup = BeautifulSoup(raw_html, "html.parser")
    text = soup.get_text(separator=" ", strip=True)
    return " ".join(text.split())

def fetch_and_parse_xml():
    """Pobiera XML z IdoSell i wyciąga potrzebne dane"""
    print("Pobieranie pliku XML z IdoSell...")
    response = requests.get(XML_URL)
    response.raise_for_status()
    
    print("Parsowanie pliku XML...")
    root = ET.fromstring(response.content)
    
    products = []
    
    for item in root.findall('./channel/item'):
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

        # Zbieranie dodatkowych zdjęć w jeden tekst oddzielony przecinkami
        additional_images = [node.text for node in item.findall('g:additional_image_link', NS) if node.text]
        additional_images_str = ",".join(additional_images)

        product = {
            'item_id': get_g_text('id'),
            'title': get_g_text('title'),
            'description': clean_description,
            'url': get_g_text('link'),
            'brand': get_g_text('brand'),
            'image_url': get_g_text('image_link'),
            'additional_image_urls': additional_images_str,
            'price': get_g_text('price'),
            'sale_price': '', # Zostawiamy puste, zgodnie z plikiem wzorcowym
            'availability': availability,
            'condition': get_g_text('condition'),
            'product_category': get_g_text('product_type'),
            'mpn': get_g_text('mpn'),
            'color': get_g_text('color'),
            'material': get_g_text('material'),
            
            # Wartości stałe zdefiniowane we wzorcowym pliku dla ModBiS
            'gender': 'female',
            'age_group': 'adult',
            'seller_name': 'ModBiS',
            'seller_url': 'https://modbis.pl',
            'return_policy': 'https://modbis.pl/Zwrot-towaru-zasady-cterms-pol-976.html',
            'target_countries': 'PL',
            'store_country': 'PL',
            'is_eligible_search': 'true',
            'is_eligible_checkout': 'false',
            'is_ads_eligible': 'true'
        }
        products.append(product)
        
    return products

def generate_csv_and_upload(products):
    """Generuje plik CSV w pamięci RAM i wysyła na Cloudflare R2"""
    print(f"Generowanie pliku CSV dla {len(products)} produktów...")
    
    csv_buffer = io.StringIO()
    
    # Rozszerzona lista nagłówków dopasowana 1:1 do pliku wzorcowego OpenAI
    fieldnames = [
        'item_id', 'title', 'description', 'url', 'brand', 'image_url', 
        'additional_image_urls', 'price', 'sale_price', 'availability', 
        'condition', 'product_category', 'mpn', 'color', 'material', 
        'gender', 'age_group', 'seller_name', 'seller_url', 
        'return_policy', 'target_countries', 'store_country', 
        'is_eligible_search', 'is_eligible_checkout', 'is_ads_eligible'
    ]
    
    writer = csv.DictWriter(csv_buffer, fieldnames=fieldnames, quoting=csv.QUOTE_MINIMAL)
    writer.writeheader()
    for product in products:
        writer.writerow(product)
        
    print("Łączenie z Cloudflare R2...")
    s3_client = boto3.client(
        's3',
        endpoint_url=R2_ENDPOINT_URL,
        aws_access_key_id=R2_ACCESS_KEY_ID,
        aws_secret_access_key=R2_SECRET_ACCESS_KEY,
        region_name='auto'
    )
    
    print(f"Wysyłanie pliku {CSV_FILENAME} na Cloudflare...")
    try:
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
