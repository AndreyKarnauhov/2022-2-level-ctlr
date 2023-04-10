"""
Crawler implementation
"""

import datetime
import json
import re
import shutil
from pathlib import Path
from typing import Pattern, Union

import requests
from bs4 import BeautifulSoup

from core_utils.article.article import Article
from core_utils.article.io import to_raw
from core_utils.config_dto import ConfigDTO
from core_utils.constants import ASSETS_PATH, CRAWLER_CONFIG_PATH


class IncorrectSeedURLError(Exception):
    pass


class NumberOfArticlesOutOfRangeError(Exception):
    pass


class IncorrectNumberOfArticlesError(Exception):
    pass


class IncorrectHeadersError(Exception):
    pass


class IncorrectEncodingError(Exception):
    pass


class IncorrectTimeoutError(Exception):
    pass


class IncorrectVerifyError(Exception):
    pass


class Config:
    """
    Unpacks and validates configurations
    """

    def __init__(self, path_to_config: Path) -> None:
        """
        Initializes an instance of the Config class
        """
        self.path_to_config = path_to_config
        self.config = self._extract_config_content()

        self._seed_urls = self.config.seed_urls
        self._num_articles = self.config.total_articles
        self._headers = self.config.headers
        self._encoding = self.config.encoding
        self._timeout = self.config.timeout
        self._should_verify_certificate = self.config.should_verify_certificate
        self._headless_mode = self.config.headless_mode

        self._validate_config_content()

    def _extract_config_content(self) -> ConfigDTO:
        """
        Returns config values
        """
        with open(self.path_to_config, 'r', encoding='utf-8') as config:
            f = json.load(config)
        return ConfigDTO(*[f[param] for param in ['seed_urls', 'total_articles_to_find_and_parse', 'headers',
                                                  'encoding', 'timeout', 'should_verify_certificate', 'headless_mode']])

    def _validate_config_content(self) -> None:
        """
        Ensure configuration parameters
        are not corrupt
        """
        if not isinstance(self._seed_urls, list) or not all(isinstance(url, str) for url in self._seed_urls) or \
                not all(re.search('https?://w?w?w?.', url) for url in self._seed_urls):
            raise IncorrectSeedURLError
        if not isinstance(self._num_articles, int) or isinstance(self._num_articles, bool) or self._num_articles < 1:
            raise IncorrectNumberOfArticlesError
        if self._num_articles > 150:
            raise NumberOfArticlesOutOfRangeError
        if not isinstance(self._headers, dict):
            raise IncorrectHeadersError
        if not isinstance(self._encoding, str):
            raise IncorrectEncodingError
        if not isinstance(self._timeout, int) or self._timeout <= 0 or self._timeout >= 60:
            raise IncorrectTimeoutError
        if not isinstance(self._should_verify_certificate, bool) or not isinstance(self._headless_mode, bool):
            raise IncorrectVerifyError

    def get_seed_urls(self) -> list[str]:
        """
        Retrieve seed urls
        """
        return self.config.seed_urls

    def get_num_articles(self) -> int:
        """
        Retrieve total number of articles to scrape
        """
        return self.config.total_articles

    def get_headers(self) -> dict[str, str]:
        """
        Retrieve headers to use during requesting
        """
        return self.config.headers

    def get_encoding(self) -> str:
        """
        Retrieve encoding to use during parsing
        """
        return self.config.encoding

    def get_timeout(self) -> int:
        """
        Retrieve number of seconds to wait for response
        """
        return self.config.timeout

    def get_verify_certificate(self) -> bool:
        """
        Retrieve whether to verify certificate
        """
        return self.config.should_verify_certificate

    def get_headless_mode(self) -> bool:
        """
        Retrieve whether to use headless mode
        """
        return self.config.headless_mode


def make_request(url: str, config: Config) -> requests.models.Response:
    """
    Delivers a response from a request
    with given configuration
    """
    return requests.get(url, headers=config.get_headers(), timeout=config.get_timeout())


class Crawler:
    """
    Crawler implementation
    """

    url_pattern: Union[Pattern, str]

    def __init__(self, config: Config) -> None:
        """
        Initializes an instance of the Crawler class
        """
        self.config = config
        self.urls = []

    def _extract_url(self, article_bs: BeautifulSoup) -> str:
        """
        Finds and retrieves URL from HTML
        """
        for a in article_bs.find_all('a'):
            url = a.get('href')
            if url and url.startswith('/news/'):
                yield url

    def find_articles(self) -> None:
        """
        Finds articles
        """
        for seed_url in self.get_search_urls():
            page = make_request(seed_url, self.config)
            for url in self._extract_url(BeautifulSoup(page.text, 'lxml')):
                if len(self.urls) >= self.config.get_num_articles():
                    return None
                if (full_url := 'https://ptzgovorit.ru' + url) not in self.urls:
                    self.urls.append(full_url)

    def get_search_urls(self) -> list:
        """
        Returns seed_urls param
        """
        return self.config.get_seed_urls()


class HTMLParser:
    """
    ArticleParser implementation
    """

    def __init__(self, full_url: str, article_id: int, config: Config) -> None:
        """
        Initializes an instance of the HTMLParser class
        """
        self.article = Article(full_url, article_id)
        self.full_url = full_url
        self.article_id = article_id
        self.config = config

    def _fill_article_with_text(self, article_soup: BeautifulSoup) -> None:
        """
        Finds text of article
        """
        text_div = article_soup.find(
            'div', {'class': 'field field-name-body field-type-text-with-summary field-label-hidden'})
        self.article.text = '\n'.join(text for paragraph in text_div.find_all('p') if (text := paragraph.text.strip()))

    def _fill_article_with_meta_information(self, article_soup: BeautifulSoup) -> None:
        """
        Finds meta information of article
        """
        title = article_soup.find('h2', {'class': 'node-title'})
        self.article.title = title.text
        date = article_soup.find('div', {'class': 'node-date'})
        self.article.date = self.unify_date_format(date.text)
        text_div = article_soup.find(
            'div', {'class': 'field field-name-body field-type-text-with-summary field-label-hidden'})

        if author := text_div.find(string=re.compile('^Текст: ')):
            self.article.author = ' '.join(author.text.split()[1:3])
        elif author := text_div.find(string=re.compile('^Текст и фото: ')):
            self.article.author = ' '.join(author.text.split()[4:6])
        else:
            self.article.author = 'NOT FOUND'

    def unify_date_format(self, date_str: str) -> datetime.datetime:
        """
        Unifies date format
        """
        months_substitutions = {'января': 'Jan', 'февраля': 'Feb', 'марта': 'Mar', 'апреля': 'Apr', 'мая': 'May',
                                'июня': 'Jun', 'июля': 'Jul', 'августа': 'Aug', 'сентября': 'Sep', 'октября': 'Oct',
                                'ноября': 'Nov', 'декабря': 'Dec'}
        date = date_str.split()
        date[1] = months_substitutions[date[1]]
        return datetime.datetime.strptime(' '.join(date), '%d %b %Y, %H:%M')

    def parse(self) -> Union[Article, bool, list]:
        """
        Parses each article
        """
        page = make_request(self.full_url, self.config)
        article_bs = BeautifulSoup(page.text, 'lxml')
        self._fill_article_with_text(article_bs)
        self._fill_article_with_meta_information(article_bs)
        return self.article


def prepare_environment(base_path: Union[Path, str]) -> None:
    """
    Creates ASSETS_PATH folder if no created and removes existing folder
    """
    assets_path = base_path / ASSETS_PATH
    if assets_path.exists():
        if assets_path.is_dir():
            if assets_path.glob('**'):
                shutil.rmtree(assets_path)
        else:
            assets_path.unlink()
    assets_path.mkdir(exist_ok=True, parents=True)


def main() -> None:
    """
    Entrypoint for scrapper module
    """
    configuration = Config(path_to_config=CRAWLER_CONFIG_PATH)
    crawler = Crawler(config=configuration)
    print('Searching')
    crawler.find_articles()
    print('Parsing')
    for i, article_url in enumerate(crawler.urls):
        print(i)
        parser = HTMLParser(full_url=article_url, article_id=i, config=configuration)
        article = parser.parse()
        to_raw(article)


if __name__ == "__main__":
    main()
