"""One parser per public page type, shared by every town that uses it."""

from scraper.parsers.bidnet import parse_bidnet
from scraper.parsers.civicplus import enrich_civicplus_detail, parse_civicplus
from scraper.parsers.documents import parse_document_page
from scraper.parsers.wordpress import enrich_wordpress_post, parse_wordpress_list

__all__ = [
    "enrich_civicplus_detail",
    "enrich_wordpress_post",
    "parse_bidnet",
    "parse_civicplus",
    "parse_document_page",
    "parse_wordpress_list",
]
