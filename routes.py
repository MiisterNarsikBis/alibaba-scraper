"""The 12 Alibaba endpoints. Every path is served with and without the
`/alibaba` prefix, so code generated against the hosted API on RapidAPI
(paths like /products/search) runs unchanged against this server."""
from bottle import route, request

from alibaba import cache_config as ttl
from alibaba import schemas
from alibaba.discovery import autocomplete, categories, trending_keywords
from alibaba.product import product_details, product_reviews
from alibaba.search import search_by_image, search_products, search_suppliers
from alibaba.supplier import supplier_details, supplier_products, supplier_reviews
from route_glue import json_response, route_table
import usage_log

ENDPOINTS = [
    # path, schema, impl, paginated, cache ttl
    ("/products/details", schemas.ProductDetailsSchema, product_details, False, ttl.PRODUCT_DETAIL_CACHE),
    ("/products/search", schemas.ProductSearchSchema, search_products, True, ttl.PRODUCT_SEARCH_CACHE),
    ("/products/by-category", schemas.ProductsByCategorySchema, search_products, True, ttl.CATEGORY_LISTING_CACHE),
    ("/products/search-by-image", schemas.ImageSearchSchema, search_by_image, True, ttl.IMAGE_SEARCH_CACHE),
    ("/products/reviews", schemas.ProductReviewsSchema, product_reviews, True, ttl.REVIEWS_CACHE),
    ("/autocomplete", schemas.AutocompleteSchema, autocomplete, False, ttl.AUTOCOMPLETE_CACHE),
    ("/categories", schemas.CategoriesSchema, categories, False, ttl.CATEGORIES_CACHE),
    ("/trending-keywords", schemas.TrendingSchema, trending_keywords, False, ttl.TRENDING_CACHE),
    ("/suppliers/search", schemas.SupplierSearchSchema, search_suppliers, True, ttl.SUPPLIER_SEARCH_CACHE),
    ("/suppliers/details", schemas.SupplierDetailsSchema, supplier_details, False, ttl.SUPPLIER_DETAIL_CACHE),
    ("/suppliers/products", schemas.SupplierProductsSchema, supplier_products, True, ttl.SUPPLIER_PRODUCTS_CACHE),
    ("/suppliers/reviews", schemas.SupplierReviewsSchema, supplier_reviews, True, ttl.REVIEWS_CACHE),
]

register = route_table("alibaba")
for _path, _schema, _impl, _paginated, _cache in ENDPOINTS:
    register(_path, _schema, _impl, paginated=_paginated, cache=_cache)
    register("/alibaba" + _path, _schema, _impl, paginated=_paginated, cache=_cache)


@route("/", method="GET")
@route("/health", method="GET")
def health():
    return json_response({"status": "ok", "endpoints": [p for p, *_ in ENDPOINTS]})


# Usage log: registered both under /usage and /alibaba/usage to mirror the
# other endpoints. No schema validation — these are admin endpoints, no query
# params other than `limit` on /usage/recent.
def _usage_stats():
    return json_response(usage_log.stats())


def _usage_recent():
    raw = {key: request.query.getunicode(key) for key in request.query.keys()}
    try:
        limit = max(1, min(int(raw.get("limit") or 100), 1000))
    except (TypeError, ValueError):
        limit = 100
    return json_response({"entries": usage_log.snapshot(limit)})


def _usage_clear():
    usage_log.clear()
    return json_response({"cleared": True})


for _path, _handler in (
    ("/usage", _usage_stats),
    ("/usage/recent", _usage_recent),
    ("/usage/clear", _usage_clear),
):
    route(_path, method="GET")(_handler)
    route("/alibaba" + _path, method="GET")(_handler)
