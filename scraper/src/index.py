"""
DocSearch scraper main entry point
"""
import os
import json
import requests
import tempfile
from requests_iap import IAPAuth
from keycloak.realm import KeycloakRealm

from scrapy.crawler import CrawlerProcess

from typesense_helper import TypesenseHelper
from config.config_loader import ConfigLoader
from documentation_spider import DocumentationSpider
from strategies.default_strategy import DefaultStrategy
from custom_downloader_middleware import CustomDownloaderMiddleware
from custom_dupefilter import CustomDupeFilter
from config.browser_handler import BrowserHandler

try:
    # disable boto (S3 download)
    from scrapy import optional_features

    if 'boto' in optional_features:
        optional_features.remove('boto')
except ImportError:
    pass

EXIT_CODE_NO_RECORD = 3


def run_config(config):
    config = ConfigLoader(config)
    CustomDownloaderMiddleware.driver = config.driver
    DocumentationSpider.NB_INDEXED = 0

    strategy = DefaultStrategy(config)

    typesense_helper = TypesenseHelper(
        config.index_name,
        config.index_name_tmp,
        config.custom_settings
    )
    # typesense_helper.create_tmp_collection()

    root_module = 'scraper.src.' if __name__ == '__main__' else 'scraper.src.'
    DOWNLOADER_MIDDLEWARES_PATH = root_module + 'custom_downloader_middleware.' + CustomDownloaderMiddleware.__name__
    DUPEFILTER_CLASS_PATH = root_module + 'custom_dupefilter.' + CustomDupeFilter.__name__

    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en",
    }  # Defaults for scrapy https://docs.scrapy.org/en/latest/topics/settings.html#default-request-headers

    # Cloudflare Zero Trust (CF)
    if (os.getenv("CF_ACCESS_CLIENT_ID") and 
        os.getenv("CF_ACCESS_CLIENT_SECRET")):
        headers.update(
            {
                "CF-Access-Client-Id": os.getenv("CF_ACCESS_CLIENT_ID"),
                "CF-Access-Client-Secret": os.getenv("CF_ACCESS_CLIENT_SECRET"),
            }
        )

    # Google Identity-Aware Proxy (IAP)
    elif (os.getenv("IAP_AUTH_CLIENT_ID") and 
        os.getenv("IAP_AUTH_SERVICE_ACCOUNT_JSON")):
        iap_token = IAPAuth(
            client_id=os.getenv("IAP_AUTH_CLIENT_ID"),
            service_account_secret_dict=json.loads(
                os.getenv("IAP_AUTH_SERVICE_ACCOUNT_JSON")
            ),
        )(requests.Request()).headers["Authorization"]
        headers.update({"Authorization": iap_token})

    # Keycloak (KC)
    elif (os.getenv("KC_URL") and
        os.getenv("KC_REALM") and
        os.getenv("KC_CLIENT_ID") and
        os.getenv("KC_CLIENT_SECRET")):
        realm = KeycloakRealm(
            server_url=os.getenv("KC_URL"),
            realm_name=os.getenv("KC_REALM"))
        oidc_client = realm.open_id_connect(
            client_id=os.getenv("KC_CLIENT_ID"),
            client_secret=os.getenv("KC_CLIENT_SECRET"))
        token_response = oidc_client.client_credentials()
        token = token_response["access_token"]
        headers.update({"Authorization": 'bearer ' + token})

    DEFAULT_REQUEST_HEADERS = headers

    process = CrawlerProcess({
        'LOG_ENABLED': '1',
        'LOG_LEVEL': 'ERROR',
        'USER_AGENT': config.user_agent,
        'DOWNLOADER_MIDDLEWARES': {DOWNLOADER_MIDDLEWARES_PATH: 900},
        # Need to be > 600 to be after the redirectMiddleware
        'DUPEFILTER_USE_ANCHORS': config.use_anchors,
        # Use our custom dupefilter in order to be scheme agnostic regarding link provided
        'DUPEFILTER_CLASS': DUPEFILTER_CLASS_PATH,
        'DEFAULT_REQUEST_HEADERS': DEFAULT_REQUEST_HEADERS,
        'TELNETCONSOLE_ENABLED': False
    })

    process.crawl(
        DocumentationSpider,
        config=config,
        typesense_helper=typesense_helper,
        strategy=strategy
    )

    process.start()
    process.stop()

    # Kill browser if needed
    BrowserHandler.destroy(config.driver)

    if len(config.extra_records) > 0:
        typesense_helper.add_records(config.extra_records, "Extra records", False)

    print("")

    if DocumentationSpider.NB_INDEXED > 0:
        typesense_helper.commit_tmp_collection()
        print('Nb hits: {}'.format(DocumentationSpider.NB_INDEXED))
        config.update_nb_hits_value(DocumentationSpider.NB_INDEXED)
    else:
        print('Crawling issue: nbHits 0 for ' + config.index_name)
        exit(EXIT_CODE_NO_RECORD)
    print("")


if __name__ == '__main__':
    from os import environ

    config = {
            "index_name": "curling",
            "start_urls": [
                "https://curling.io/docs/"
            ],
            "sitemap_urls": [
                "https://curling.io/sitemap.xml"
            ],
            "sitemap_alternate_links": True,
            "stop_urls": [],
            "selectors": {
                "lvl0": {
                    "selector": ".menu__link--sublist.menu__link--active",
                    "global": True,
                    "default_value": "Documentation"
                },
                "lvl1": "header h1",
                "lvl2": "article h2",
                "lvl3": "article h3",
                "lvl4": "article h4",
                "lvl5": "article h5, article td:first-child",
                "text": "article p, article li, article td:last-child"
            },
            "strip_chars": " .,;:#",
            "custom_settings": {
                "separatorsToIndex": "_",
                "attributesForFaceting": [
                    "language",
                    "version",
                    "type"
                ],
                "attributesToRetrieve": [
                    "hierarchy",
                    "content",
                    "anchor",
                    "url",
                    "url_without_anchor",
                    "type"
                ]
            },
            "conversation_id": [
                "1325495026"
            ],
            "nb_hits": 698
        }

    with tempfile.NamedTemporaryFile(mode="w", delete=False) as temp_file:
        json.dump(config, temp_file, indent=4)

    with open(temp_file.name, "w") as json_file:
        json.dump(config, json_file, indent=4)  # Optional: Use 'indent' for pretty formatting

    run_config(temp_file.name)
