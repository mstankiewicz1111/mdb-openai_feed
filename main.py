import os
import csv
import re
import html
import requests
import xml.etree.ElementTree as ET
import boto3
from botocore.exceptions import ClientError

# Konfiguracja ze zmiennych środowiskowych
XML_URL = os.environ.get('IDOSELL_XML_URL')
R2_ENDPOINT_URL = os.environ.get('R2_ENDPOINT_URL')
R2_ACCESS_KEY_ID = os.environ.get('R2_ACCESS_KEY_ID')
R2_SECRET_ACCESS_KEY = os.environ.get('R2_SECRET_ACCESS_KEY')
R2_BUCKET_NAME = os.environ.get('R2_BUCKET_NAME', 'openai-product-feed')
CSV_FILENAME = 'feed.csv'

# Ścieżki do plików tymczasowych na dysku
TEMP_XML_FILE = 'temp_feed.xml'
TEMP_CSV_FILE = 'temp_feed.csv'

NS_G = '{http://base.google.com/ns/1.0}'

def clean_html(raw_html):
    """Błyskawiczne czyszczenie HTML z minimalnym zużyciem pamięci RAM"""
    if not raw_html:
        return ""
    # Zamiana tagów blokowych na spacje, by słowa się nie sklejały
    text = re.sub(r'</?(?:p|div|h\d|li|br|ul|ol|tr|td)[^>]*>', ' ', raw_html, flags=re.IGNORECASE)
    # Usunięcie pozostałych tagów HTML
    text = re.sub(r'<[^>]+>', ' ', text)
    # Rozkodowanie encji HTML (np. &oacute;, &nbsp;)
    text = html.unescape(text)
    # Usunięcie zbędnych wielokrotnych spacji
    return " ".join(text.split())

def download_xml_stream(url, target_path):
    """Pobiera plik XML strumieniowo w kawałkach, nie obciążając RAM-u"""
    print("Pobieranie pliku XML z IdoSell strumieniowo na dysk...")
    with requests.get(url, stream=True) as response:
        response.raise_for_status()
        with open(target_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=65536):
                if chunk:
                    f.write(chunk)
    print("Pobrano plik XML na dysk.")

def convert_xml_to_csv_stream(xml_path, csv_path):
    """Przetwarza XML strumieniowo element po elemencie i od razu zapisuje CSV"""
    print("Rozpoczynanie strumieniowej konwersji XML -> CSV...")
    
    fieldnames = [
        'item_id', 'title', 'description', 'url', 'brand', 'image_url', 
        'additional_image_urls', 'price', 'sale_price', 'availability', 
        'condition', 'product_category', 'mpn', 'color', 'material', 
        'gender', 'age_group', 'seller_name', 'seller_url', 
        'return_policy', 'target_countries', 'store_country', 
        'is_eligible_search', 'is_eligible_checkout', 'is_ads_eligible'
    ]
    
    count = 0
    with open(csv_path, 'w', newline='', encoding='utf-8') as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames, quoting=csv.QUOTE_MINIMAL)
        writer.writeheader()
        
        # iterparse pozwala czytać wielkie pliki bez ładowania całości do RAM
        context = ET.iterparse(xml_path, events=('start', 'end'))
        _, root = next(context)
        
        for event, elem in context:
            if event == 'end' and elem.tag == 'item':
                def get_g_text(tag_name):
                    node = elem.find(f'{NS_G}{tag_name}')
                    return node.text.strip() if (node is not None and node.text) else ""

                availability = get_g_text('availability')
                if availability == 'in stock':
                    availability = 'in_stock'
                elif availability == 'out of stock':
                    availability = 'out_of_stock'

                additional_images = [node.text.strip() for node in elem.findall(f'{NS_G}additional_image_link') if node.text]
                additional_images_str = ",".join(additional_images)

                row = {
                    'item_id': get_g_text('id'),
                    'title': get_g_text('title'),
                    'description': clean_html(get_g_text('description')),
                    'url': get_g_text('link'),
                    'brand': get_g_text('brand'),
                    'image_url': get_g_text('image_link'),
                    'additional_image_urls': additional_images_str,
                    'price': get_g_text('price'),
                    'sale_price': '',
                    'availability': availability,
                    'condition': get_g_text('condition'),
                    'product_category': get_g_text('product_type'),
                    'mpn': get_g_text('mpn'),
                    'color': get_g_text('color'),
                    'material': get_g_text('material'),
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
                
                writer.writerow(row)
                count += 1
                
                # KLUCZOWE: zwalniamy pamięć natychmiast po zapisaniu wiersza
                elem.clear()
                root.clear()

    print(f"Pomyślnie przetworzono i zapisano {count} produktów do pliku CSV.")

def upload_csv_to_r2(csv_path):
    """Wysyła plik CSV z dysku na Cloudflare R2 w małych częściach"""
    print("Łączenie z Cloudflare R2...")
    s3_client = boto3.client(
        's3',
        endpoint_url=R2_ENDPOINT_URL,
        aws_access_key_id=R2_ACCESS_KEY_ID,
        aws_secret_access_key=R2_SECRET_ACCESS_KEY,
        region_name='auto'
    )
    
    print(f"Wysyłanie {CSV_FILENAME} do Cloudflare R2...")
    try:
        s3_client.upload_file(
            Filename=csv_path,
            Bucket=R2_BUCKET_NAME,
            Key=CSV_FILENAME,
            ExtraArgs={'ContentType': 'text/csv; charset=utf-8'}
        )
        print("✅ Sukces! Plik CSV został zaktualizowany na Cloudflare.")
    except ClientError as e:
        print(f"❌ Błąd podczas wysyłania do R2: {e}")
        raise

def cleanup():
    """Usuwa pliki tymczasowe po zakończeniu pracy"""
    for temp_file in [TEMP_XML_FILE, TEMP_CSV_FILE]:
        if os.path.exists(temp_file):
            os.remove(temp_file)
    print("Wyczyszczono pliki tymczasowe.")

if __name__ == "__main__":
    try:
        download_xml_stream(XML_URL, TEMP_XML_FILE)
        convert_xml_to_csv_stream(TEMP_XML_FILE, TEMP_CSV_FILE)
        upload_csv_to_r2(TEMP_CSV_FILE)
    except Exception as e:
        print(f"Wystąpił błąd krytyczny: {e}")
    finally:
        cleanup()
