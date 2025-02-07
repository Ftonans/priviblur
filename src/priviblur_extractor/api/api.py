"""Tumblr API Wrapper

Inspired by Invidious' version for YouTube

"""
import json
import urllib.parse
from typing import Optional

import aiohttp

from . import request_config as rconf
from .. import helpers
from ..helpers import exceptions

logger = helpers.LOGGER.getChild("api")


class TumblrAPI:
    config = rconf

    DEFAULT_HEADERS = {
        "accept": "application/json;format=camelcase",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/104.0.0.0 Safari/537.36",
        "accept-encoding": "gzip, deflate",

        # Authorization token
        "authorization": "Bearer aIcXSOoTtqrzR8L8YEIOmBeW94c3FmbSNSWAUbxsny9KKx5VFh"
    }

    @classmethod
    async def create(cls, client=None, main_request_timeout=10, json_loads=json.loads):
        """Creates a Tumblr API instance with the given client. Automatically creates a client obj if not given."""
        if not client:
            main_request_timeout = aiohttp.ClientTimeout(main_request_timeout)

            client = aiohttp.ClientSession(
                "https://www.tumblr.com",
                headers=cls.DEFAULT_HEADERS,
                timeout=main_request_timeout  # TODO allow fine-tuning the different types of timeouts
            )

        return cls(client, json_loads)

    def __init__(self, client: aiohttp.ClientSession, json_loads=json.loads):
        """Initializes a TumblrAPI instance with the given client"""
        self.client = client
        self.json_loader = json_loads

    async def _get_json(self, endpoint, url_params=None):
        """Internal method that does the actual request to Tumblr"""
        if url_params:
            url = f"{endpoint}?{urllib.parse.urlencode(url_params)}"
        else:
            url = f"{endpoint}"

        # When logging, are we able to prettyprint the output? If so we shall
        try:
            import prettyprinter
            _format = prettyprinter.pformat
        except ImportError:
            def _format(obj): return obj

        logger.info(f"Requesting endpoint: /api/v2/{url}")

        response = await self.client.get(f"/api/v2/{url}")

        logger.debug(f"Requested endpoint: /api/v2/{url}")

        try:
            result = await response.json(loads=self.json_loader)
        except Exception as e:
            logger.error("Failed to parse JSON response from Tumblr!")
            logger.error(f"Got error: '{type(e).__name__}'. Reason: '{getattr(e, 'message', '')}'")

            raise exceptions.InitialTumblrAPIParseException(getattr(e, 'message', ''))

        # Invalid response handling
        if response.status != 200:
            message = result["meta"]["msg"]
            code = result["meta"]["status"]

            logger.info(f"Error response received with HTTP status code: {code}")
            logger.debug(f"Response headers: {_format(response.headers)}")

            if error := result.get("errors"):
                details = error[0].get('detail')
                internal_code = error[0].get("code")
                logger.info(f"Reason: {details}")
                logger.info(f"Tumblr internal error code: {internal_code}")
            else:
                internal_code = None
                details = ""

            match internal_code:
                case 13001:
                    raise exceptions.TumblrRestrictedTagError(message, code, details, internal_code)
                case 4012:
                    raise exceptions.TumblrLoginRequiredError(message, code, details, internal_code)
                case 0:
                    raise exceptions.TumblrBlogNotFoundError(message, code, details, internal_code)
                case _:
                    logger.error(f"Unknown tumblr internal error code: {internal_code}")
                    raise exceptions.TumblrErrorResponse(message, code, details, internal_code)

        return result

    async def explore(self):
        """Access the /explore endpoint"""
        return await self._get_json("explore")

    async def explore_trending(self, *, continuation: Optional[str] = None):
        """Requests the /explore/trending endpoint"""

        url_parameters : dict = {"reblog_info": "true"}

        if continuation:
            url_parameters["cursor"] = continuation

        url_parameters["fields[blogs]"] = rconf.EXPLORE_BLOG_INFO_FIELDS

        return await self._get_json("explore/trending", url_parameters)

    async def explore_today(self, *, continuation: Optional[str] = None):
        """Requests the /explore/home/today endpoint"""

        url_parameters = {
            "fields[blogs]": rconf.EXPLORE_BLOG_INFO_FIELDS,
            "reblog_info": "true",
        }

        if continuation:
            url_parameters["cursor"] = continuation

        return await self._get_json("explore/home/today", url_parameters)
    
    async def explore_post(self, post_type: rconf.ExplorePostTypeFilters, *, continuation: Optional[str] = None):
        """Requests the /explore/posts/<post-type> endpoint with a post type, to get a trending posts of said type"""
        url_parameters : dict = {"reblog_info": "true"}

        if continuation:
            url_parameters["cursor"] = continuation

        url_parameters["fields[blogs]"] = rconf.EXPLORE_BLOG_INFO_FIELDS

        return await self._get_json(f"explore/posts/{post_type.name.lower()}", url_parameters)

    async def timeline_search(self, query: str, timeline_type: rconf.TimelineType, *,
                              continuation: Optional[str] = None,
                              latest: bool = False, days: int = 0,
                              post_type_filter: Optional[rconf.ExplorePostTypeFilters] = None):
        """Requests the /timeline/search endpoint

        Parameters:
            query: Search Query
            continuation: Continuation token for fetching the next batch of content

            timeline_type: Specific timeline type to return. Can be TAG, BLOG or POST
            latest: Whether to filter results by "latest" or most popular
            days:  Only return content that are posted X days prior. 0 to disable this filter.
            post_type_filter: If set, only return posts of the given type.
        """
        url_parameters = {
            "limit": 20,
            "days": days,
            "query": query,

            "mode": "top" if not latest else "recent"
        }

        # Special handling
        if timeline_type == rconf.TimelineType.POST:
            url_parameters["timeline_type"] = "post"
            url_parameters["skip_component"] = "related_tags,blog_search"
        else:
            url_parameters["timeline_type"] = timeline_type.name.lower()

        url_parameters["reblog_info"] = "true"

        if post_type_filter:
            url_parameters["post_type_filter"] = post_type_filter.name.lower()

        url_parameters["fields[blogs]"] = rconf.TUMBLR_SEARCH_BLOG_INFO_FIELDS

        # Cursor goes after "blog[fields]"
        if continuation:
            url_parameters["cursor"] = continuation

        return await self._get_json(f"timeline/search", url_parameters)

    async def hubs_timeline(self, tag: str, *, continuation: Optional[str], latest: bool = False):
        """Requests the /hubs/<tag>/timeline endpoint

        Parameters:
            tag: tag to query

            continuation: Continuation token for fetching the next batch of content
            latest: Whether to filter results by "latest" or most popular
        """

        url_parameters = {
            "fields[blogs]": rconf.TUMBLR_TAG_BLOG_INFO_FIELDS,
            "sort": "top" if not latest else "recent",
            "limit": 14,
        }

        if continuation:
            url_parameters["hub_name"] = tag
            url_parameters["rawurldecode"] = 1
            url_parameters["skip_header"] = 1

            url_parameters["cursor"] = continuation

        return await self._get_json(f"hubs/{urllib.parse.quote(tag, safe='')}/timeline", url_parameters)

    async def blog_posts(self, blog_name, continuation = None, tag = None, post_type = None, before_id = None):
        """Requests the /blog/<blog name>/posts endpoint

        Parameters:
            blog_name: the blog the post is from

            continuation: Continuation token for fetching the next batch of content
            tag: Search posts tagged with a tag within the blog
            post_type: Filter by post type when browsing tags or searching
            before_id: Returns posts before the given ID
        """

        url_parameters = {
         "fields[blogs]": rconf.BLOG_POSTS_BLOG_INFO_FIELDS,
         "npf": "true",
         "reblog_info": "true",
         "include_pinned_posts": "true"
        }

        if tag:
            url_parameters["tag"] = tag

            if post_type:
                url_parameters["post_type"] = post_type

        if before_id:
            url_parameters["before_id"] = before_id

        if continuation:
            url_parameters["tumblelog"] = blog_name
            url_parameters["page_number"] = continuation

        return await self._get_json(f"blog/{urllib.parse.quote(blog_name, safe = '')}/posts", url_params=url_parameters)

    async def blog_search(self, blog_name, query, *, page = None,
                          top = None, original_posts = None, post_type = None):
        """Requests the /blog/<blog name>/search/<query> endpoint
            Parameters:
                blog_name: name of the blog to search
                query: search query

                continuation: Continuation token for fetching the next batch of content
                top: Whether or not to sort by popularity
                original_posts: Whether or not the search should only return original posts by the blog
                post_type: Filter by post type
        """
        url_parameters = {
         "reblog_info": "true",
         "fields[blogs]": rconf.BLOG_SEARCH_BLOG_INFO_FIELDS,
        }

        if post_type:
            url_parameters["post_type"] = post_type

        url_parameters["npf"] = "true"

        if original_posts:
            url_parameters["post_role"] = "ORIGINAL"

        if top:
            url_parameters["sort"] = "POPULARITY_DESC"
        else:
            url_parameters["sort"] = "CREATED_DESC"

        # The following can vary on Tumblr. Some continuation requests won't have query or rawurldecode
        # for example while next_offset can be incremented by something other than 10.
        #
        # Though for simplicities sake,  we'll just assume that everything is present and next_offset can
        # only be incremented by 10.
        #
        # This allows us to just have a simple page attribute
        if page:
            url_parameters["query"] = query
            url_parameters["rawurldecode"] = 1
            url_parameters["next_offset"] = 10 * (page - 1)

        return await self._get_json(f"blog/{urllib.parse.quote(blog_name, safe = '')}/search/{urllib.parse.quote(query)}", url_params=url_parameters)

    async def blog_post(self, blog_name, post_id):
        """Requests the /blog/<blog name>/posts/<post id> endpoint

        Parameters:
            blog_name: the blog the post is from
            post_id: the id of the post
        """

        return await self._get_json(
            f"blog/{urllib.parse.quote(blog_name, safe='')}/posts/{post_id}/permalink",
            url_params={"fields[blogs]": rconf.POST_BLOG_INFO_FIELDS, "reblog_info": "true"}
        )

    async def poll_results(self, blog_name, post_id, poll_id):
        """Requests the /polls/<blog name>/<post id>/<poll_id>/results endpoint

        Parameters:
            blog_name: the blog the post is from
            post_id: the id of the post
            poll_id: the id of the poll
        """

        return await self._get_json(
            f"polls/{urllib.parse.quote(blog_name, safe='')}/{post_id}/{poll_id}/results",
        )
