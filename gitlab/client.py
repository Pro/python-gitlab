# -*- coding: utf-8 -*-
#
# Copyright (C) 2013-2017 Gauvain Pocentek <gauvain@pocentek.net>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
"""Wrapper for the GitLab API."""

import time
from typing import cast, Any, Dict, List, Optional, Tuple, TYPE_CHECKING, Union

import requests
import requests.utils

import gitlab.config
import gitlab.const
import gitlab.exceptions
from gitlab import utils
from requests_toolbelt.multipart.encoder import MultipartEncoder  # type: ignore


REDIRECT_MSG = (
    "python-gitlab detected an http to https redirection. You "
    "must update your GitLab URL to use https:// to avoid issues."
)


class Gitlab(object):
    """Represents a GitLab server connection.

    Args:
        url (str): The URL of the GitLab server.
        private_token (str): The user private token
        oauth_token (str): An oauth token
        job_token (str): A CI job token
        ssl_verify (bool|str): Whether SSL certificates should be validated. If
            the value is a string, it is the path to a CA file used for
            certificate validation.
        timeout (float): Timeout to use for requests to the GitLab server.
        http_username (str): Username for HTTP authentication
        http_password (str): Password for HTTP authentication
        api_version (str): Gitlab API version to use (support for 4 only)
        pagination (str): Can be set to 'keyset' to use keyset pagination
        order_by (str): Set order_by globally
        user_agent (str): A custom user agent to use for making HTTP requests.
    """

    def __init__(
        self,
        url: str,
        private_token: Optional[str] = None,
        oauth_token: Optional[str] = None,
        job_token: Optional[str] = None,
        ssl_verify: Union[bool, str] = True,
        http_username: Optional[str] = None,
        http_password: Optional[str] = None,
        timeout: Optional[float] = None,
        api_version: str = "4",
        session: Optional[requests.Session] = None,
        per_page: Optional[int] = None,
        pagination: Optional[str] = None,
        order_by: Optional[str] = None,
        user_agent: str = gitlab.const.USER_AGENT,
    ) -> None:

        self._api_version = str(api_version)
        self._server_version: Optional[str] = None
        self._server_revision: Optional[str] = None
        self._base_url = url.rstrip("/")
        self._url = "%s/api/v%s" % (self._base_url, api_version)
        #: Timeout to use for requests to gitlab server
        self.timeout = timeout
        #: Headers that will be used in request to GitLab
        self.headers = {"User-Agent": user_agent}

        #: Whether SSL certificates should be validated
        self.ssl_verify = ssl_verify

        self.private_token = private_token
        self.http_username = http_username
        self.http_password = http_password
        self.oauth_token = oauth_token
        self.job_token = job_token
        self._set_auth_info()

        #: Create a session object for requests
        self.session = session or requests.Session()

        self.per_page = per_page
        self.pagination = pagination
        self.order_by = order_by

        # We only support v4 API at this time
        if self._api_version not in ("4",):
            raise ModuleNotFoundError(name="gitlab.v%s.objects" % self._api_version)
        # NOTE: We must delay import of gitlab.v4.objects until now or
        # otherwise it will cause circular import errors
        import gitlab.v4.objects

        objects = gitlab.v4.objects
        self._objects = objects

        self.broadcastmessages = objects.BroadcastMessageManager(self)
        self.deploykeys = objects.DeployKeyManager(self)
        self.deploytokens = objects.DeployTokenManager(self)
        self.geonodes = objects.GeoNodeManager(self)
        self.gitlabciymls = objects.GitlabciymlManager(self)
        self.gitignores = objects.GitignoreManager(self)
        self.groups = objects.GroupManager(self)
        self.hooks = objects.HookManager(self)
        self.issues = objects.IssueManager(self)
        self.ldapgroups = objects.LDAPGroupManager(self)
        self.licenses = objects.LicenseManager(self)
        self.namespaces = objects.NamespaceManager(self)
        self.mergerequests = objects.MergeRequestManager(self)
        self.notificationsettings = objects.NotificationSettingsManager(self)
        self.projects = objects.ProjectManager(self)
        self.runners = objects.RunnerManager(self)
        self.settings = objects.ApplicationSettingsManager(self)
        self.appearance = objects.ApplicationAppearanceManager(self)
        self.sidekiq = objects.SidekiqManager(self)
        self.snippets = objects.SnippetManager(self)
        self.users = objects.UserManager(self)
        self.todos = objects.TodoManager(self)
        self.dockerfiles = objects.DockerfileManager(self)
        self.events = objects.EventManager(self)
        self.audit_events = objects.AuditEventManager(self)
        self.features = objects.FeatureManager(self)
        self.pagesdomains = objects.PagesDomainManager(self)
        self.user_activities = objects.UserActivitiesManager(self)
        self.applications = objects.ApplicationManager(self)
        self.variables = objects.VariableManager(self)
        self.personal_access_tokens = objects.PersonalAccessTokenManager(self)

    def __enter__(self) -> "Gitlab":
        return self

    def __exit__(self, *args: Any) -> None:
        self.session.close()

    def __getstate__(self) -> Dict[str, Any]:
        state = self.__dict__.copy()
        state.pop("_objects")
        return state

    def __setstate__(self, state: Dict[str, Any]) -> None:
        self.__dict__.update(state)
        # We only support v4 API at this time
        if self._api_version not in ("4",):
            raise ModuleNotFoundError(name="gitlab.v%s.objects" % self._api_version)
        # NOTE: We must delay import of gitlab.v4.objects until now or
        # otherwise it will cause circular import errors
        import gitlab.v4.objects

        self._objects = gitlab.v4.objects

    @property
    def url(self) -> str:
        """The user-provided server URL."""
        return self._base_url

    @property
    def api_url(self) -> str:
        """The computed API base URL."""
        return self._url

    @property
    def api_version(self) -> str:
        """The API version used (4 only)."""
        return self._api_version

    @classmethod
    def from_config(
        cls, gitlab_id: Optional[str] = None, config_files: Optional[List[str]] = None
    ) -> "Gitlab":
        """Create a Gitlab connection from configuration files.

        Args:
            gitlab_id (str): ID of the configuration section.
            config_files list[str]: List of paths to configuration files.

        Returns:
            (gitlab.Gitlab): A Gitlab connection.

        Raises:
            gitlab.config.GitlabDataError: If the configuration is not correct.
        """
        config = gitlab.config.GitlabConfigParser(
            gitlab_id=gitlab_id, config_files=config_files
        )
        return cls(
            config.url,
            private_token=config.private_token,
            oauth_token=config.oauth_token,
            job_token=config.job_token,
            ssl_verify=config.ssl_verify,
            timeout=config.timeout,
            http_username=config.http_username,
            http_password=config.http_password,
            api_version=config.api_version,
            per_page=config.per_page,
            pagination=config.pagination,
            order_by=config.order_by,
            user_agent=config.user_agent,
        )

    def auth(self) -> None:
        """Performs an authentication using private token.

        The `user` attribute will hold a `gitlab.objects.CurrentUser` object on
        success.
        """
        self.user = self._objects.CurrentUserManager(self).get()

    def version(self) -> Tuple[str, str]:
        """Returns the version and revision of the gitlab server.

        Note that self.version and self.revision will be set on the gitlab
        object.

        Returns:
            tuple (str, str): The server version and server revision.
                              ('unknown', 'unknwown') if the server doesn't
                              perform as expected.
        """
        if self._server_version is None:
            try:
                data = self.http_get("/version")
                if isinstance(data, dict):
                    self._server_version = data["version"]
                    self._server_revision = data["revision"]
                else:
                    self._server_version = "unknown"
                    self._server_revision = "unknown"
            except Exception:
                self._server_version = "unknown"
                self._server_revision = "unknown"

        return cast(str, self._server_version), cast(str, self._server_revision)

    @gitlab.exceptions.on_http_error(gitlab.exceptions.GitlabVerifyError)
    def lint(self, content: str, **kwargs: Any) -> Tuple[bool, List[str]]:
        """Validate a gitlab CI configuration.

        Args:
            content (txt): The .gitlab-ci.yml content
            **kwargs: Extra options to send to the server (e.g. sudo)

        Raises:
            GitlabAuthenticationError: If authentication is not correct
            GitlabVerifyError: If the validation could not be done

        Returns:
            tuple: (True, []) if the file is valid, (False, errors(list))
                otherwise
        """
        post_data = {"content": content}
        data = self.http_post("/ci/lint", post_data=post_data, **kwargs)
        if TYPE_CHECKING:
            assert not isinstance(data, requests.Response)
        return (data["status"] == "valid", data["errors"])

    @gitlab.exceptions.on_http_error(gitlab.exceptions.GitlabMarkdownError)
    def markdown(
        self, text: str, gfm: bool = False, project: Optional[str] = None, **kwargs: Any
    ) -> str:
        """Render an arbitrary Markdown document.

        Args:
            text (str): The markdown text to render
            gfm (bool): Render text using GitLab Flavored Markdown. Default is
                False
            project (str): Full path of a project used a context when `gfm` is
                True
            **kwargs: Extra options to send to the server (e.g. sudo)

        Raises:
            GitlabAuthenticationError: If authentication is not correct
            GitlabMarkdownError: If the server cannot perform the request

        Returns:
            str: The HTML rendering of the markdown text.
        """
        post_data = {"text": text, "gfm": gfm}
        if project is not None:
            post_data["project"] = project
        data = self.http_post("/markdown", post_data=post_data, **kwargs)
        if TYPE_CHECKING:
            assert not isinstance(data, requests.Response)
        return data["html"]

    @gitlab.exceptions.on_http_error(gitlab.exceptions.GitlabLicenseError)
    def get_license(self, **kwargs: Any) -> Dict[str, Any]:
        """Retrieve information about the current license.

        Args:
            **kwargs: Extra options to send to the server (e.g. sudo)

        Raises:
            GitlabAuthenticationError: If authentication is not correct
            GitlabGetError: If the server cannot perform the request

        Returns:
            dict: The current license information
        """
        result = self.http_get("/license", **kwargs)
        if isinstance(result, dict):
            return result
        return {}

    @gitlab.exceptions.on_http_error(gitlab.exceptions.GitlabLicenseError)
    def set_license(self, license: str, **kwargs: Any) -> Dict[str, Any]:
        """Add a new license.

        Args:
            license (str): The license string
            **kwargs: Extra options to send to the server (e.g. sudo)

        Raises:
            GitlabAuthenticationError: If authentication is not correct
            GitlabPostError: If the server cannot perform the request

        Returns:
            dict: The new license information
        """
        data = {"license": license}
        result = self.http_post("/license", post_data=data, **kwargs)
        if TYPE_CHECKING:
            assert not isinstance(result, requests.Response)
        return result

    def _set_auth_info(self) -> None:
        tokens = [
            token
            for token in [self.private_token, self.oauth_token, self.job_token]
            if token
        ]
        if len(tokens) > 1:
            raise ValueError(
                "Only one of private_token, oauth_token or job_token should "
                "be defined"
            )
        if (self.http_username and not self.http_password) or (
            not self.http_username and self.http_password
        ):
            raise ValueError(
                "Both http_username and http_password should " "be defined"
            )
        if self.oauth_token and self.http_username:
            raise ValueError(
                "Only one of oauth authentication or http "
                "authentication should be defined"
            )

        self._http_auth = None
        if self.private_token:
            self.headers.pop("Authorization", None)
            self.headers["PRIVATE-TOKEN"] = self.private_token
            self.headers.pop("JOB-TOKEN", None)

        if self.oauth_token:
            self.headers["Authorization"] = "Bearer %s" % self.oauth_token
            self.headers.pop("PRIVATE-TOKEN", None)
            self.headers.pop("JOB-TOKEN", None)

        if self.job_token:
            self.headers.pop("Authorization", None)
            self.headers.pop("PRIVATE-TOKEN", None)
            self.headers["JOB-TOKEN"] = self.job_token

        if self.http_username:
            self._http_auth = requests.auth.HTTPBasicAuth(
                self.http_username, self.http_password
            )

    def enable_debug(self) -> None:
        import logging

        from http.client import HTTPConnection  # noqa

        HTTPConnection.debuglevel = 1  # type: ignore
        logging.basicConfig()
        logging.getLogger().setLevel(logging.DEBUG)
        requests_log = logging.getLogger("requests.packages.urllib3")
        requests_log.setLevel(logging.DEBUG)
        requests_log.propagate = True

    def _create_headers(self, content_type: Optional[str] = None) -> Dict[str, Any]:
        request_headers = self.headers.copy()
        if content_type is not None:
            request_headers["Content-type"] = content_type
        return request_headers

    def _get_session_opts(self, content_type: str) -> Dict[str, Any]:
        return {
            "headers": self._create_headers(content_type),
            "auth": self._http_auth,
            "timeout": self.timeout,
            "verify": self.ssl_verify,
        }

    def _build_url(self, path: str) -> str:
        """Returns the full url from path.

        If path is already a url, return it unchanged. If it's a path, append
        it to the stored url.

        Returns:
            str: The full URL
        """
        if path.startswith("http://") or path.startswith("https://"):
            return path
        else:
            return "%s%s" % (self._url, path)

    def _check_redirects(self, result: requests.Response) -> None:
        # Check the requests history to detect http to https redirections.
        # If the initial verb is POST, the next request will use a GET request,
        # leading to an unwanted behaviour.
        # If the initial verb is PUT, the data will not be send with the next
        # request.
        # If we detect a redirection to https with a POST or a PUT request, we
        # raise an exception with a useful error message.
        if result.history and self._base_url.startswith("http:"):
            for item in result.history:
                if item.status_code not in (301, 302):
                    continue
                # GET methods can be redirected without issue
                if item.request.method == "GET":
                    continue
                # Did we end-up with an https:// URL?
                location = item.headers.get("Location", None)
                if location and location.startswith("https://"):
                    raise gitlab.exceptions.RedirectError(REDIRECT_MSG)

    def http_request(
        self,
        verb: str,
        path: str,
        query_data: Optional[Dict[str, Any]] = None,
        post_data: Optional[Dict[str, Any]] = None,
        streamed: bool = False,
        files: Optional[Dict[str, Any]] = None,
        timeout: Optional[float] = None,
        obey_rate_limit: bool = True,
        retry_transient_errors: bool = False,
        max_retries: int = 10,
        **kwargs: Any,
    ) -> requests.Response:
        """Make an HTTP request to the Gitlab server.

        Args:
            verb (str): The HTTP method to call ('get', 'post', 'put',
                        'delete')
            path (str): Path or full URL to query ('/projects' or
                        'http://whatever/v4/api/projecs')
            query_data (dict): Data to send as query parameters
            post_data (dict): Data to send in the body (will be converted to
                              json)
            streamed (bool): Whether the data should be streamed
            files (dict): The files to send to the server
            timeout (float): The timeout, in seconds, for the request
            obey_rate_limit (bool): Whether to obey 429 Too Many Request
                                    responses. Defaults to True.
            retry_transient_errors (bool): Whether to retry after 500, 502,
                                           503, or 504 responses. Defaults
                                           to False.
            max_retries (int): Max retries after 429 or transient errors,
                               set to -1 to retry forever. Defaults to 10.
            **kwargs: Extra options to send to the server (e.g. sudo)

        Returns:
            A requests result object.

        Raises:
            GitlabHttpError: When the return code is not 2xx
        """
        query_data = query_data or {}
        url = self._build_url(path)

        params: Dict[str, Any] = {}
        utils.copy_dict(params, query_data)

        # Deal with kwargs: by default a user uses kwargs to send data to the
        # gitlab server, but this generates problems (python keyword conflicts
        # and python-gitlab/gitlab conflicts).
        # So we provide a `query_parameters` key: if it's there we use its dict
        # value as arguments for the gitlab server, and ignore the other
        # arguments, except pagination ones (per_page and page)
        if "query_parameters" in kwargs:
            utils.copy_dict(params, kwargs["query_parameters"])
            for arg in ("per_page", "page"):
                if arg in kwargs:
                    params[arg] = kwargs[arg]
        else:
            utils.copy_dict(params, kwargs)

        opts = self._get_session_opts(content_type="application/json")

        verify = opts.pop("verify")
        opts_timeout = opts.pop("timeout")
        # If timeout was passed into kwargs, allow it to override the default
        if timeout is None:
            timeout = opts_timeout

        # We need to deal with json vs. data when uploading files
        if files:
            json = None
            if post_data is None:
                post_data = {}
            else:
                # booleans does not exists for data (neither for MultipartEncoder):
                # cast to string int to avoid: 'bool' object has no attribute 'encode'
                for k, v in post_data.items():
                    if isinstance(v, bool):
                        post_data[k] = str(int(v))
            post_data["file"] = files.get("file")
            post_data["avatar"] = files.get("avatar")
            data = MultipartEncoder(post_data)
            opts["headers"]["Content-type"] = data.content_type
        else:
            json = post_data
            data = None

        # Requests assumes that `.` should not be encoded as %2E and will make
        # changes to urls using this encoding. Using a prepped request we can
        # get the desired behavior.
        # The Requests behavior is right but it seems that web servers don't
        # always agree with this decision (this is the case with a default
        # gitlab installation)
        req = requests.Request(verb, url, json=json, data=data, params=params, **opts)
        prepped = self.session.prepare_request(req)
        prepped.url = utils.sanitized_url(prepped.url)
        settings = self.session.merge_environment_settings(
            prepped.url, {}, streamed, verify, None
        )

        cur_retries = 0
        while True:
            result = self.session.send(prepped, timeout=timeout, **settings)

            self._check_redirects(result)

            if 200 <= result.status_code < 300:
                return result

            if (429 == result.status_code and obey_rate_limit) or (
                result.status_code in [500, 502, 503, 504] and retry_transient_errors
            ):
                if max_retries == -1 or cur_retries < max_retries:
                    wait_time = 2 ** cur_retries * 0.1
                    if "Retry-After" in result.headers:
                        wait_time = int(result.headers["Retry-After"])
                    cur_retries += 1
                    time.sleep(wait_time)
                    continue

            error_message = result.content
            try:
                error_json = result.json()
                for k in ("message", "error"):
                    if k in error_json:
                        error_message = error_json[k]
            except (KeyError, ValueError, TypeError):
                pass

            if result.status_code == 401:
                raise gitlab.exceptions.GitlabAuthenticationError(
                    response_code=result.status_code,
                    error_message=error_message,
                    response_body=result.content,
                )

            raise gitlab.exceptions.GitlabHttpError(
                response_code=result.status_code,
                error_message=error_message,
                response_body=result.content,
            )

    def http_get(
        self,
        path: str,
        query_data: Optional[Dict[str, Any]] = None,
        streamed: bool = False,
        raw: bool = False,
        **kwargs: Any,
    ) -> Union[Dict[str, Any], requests.Response]:
        """Make a GET request to the Gitlab server.

        Args:
            path (str): Path or full URL to query ('/projects' or
                        'http://whatever/v4/api/projecs')
            query_data (dict): Data to send as query parameters
            streamed (bool): Whether the data should be streamed
            raw (bool): If True do not try to parse the output as json
            **kwargs: Extra options to send to the server (e.g. sudo)

        Returns:
            A requests result object is streamed is True or the content type is
            not json.
            The parsed json data otherwise.

        Raises:
            GitlabHttpError: When the return code is not 2xx
            GitlabParsingError: If the json data could not be parsed
        """
        query_data = query_data or {}
        result = self.http_request(
            "get", path, query_data=query_data, streamed=streamed, **kwargs
        )

        if (
            result.headers["Content-Type"] == "application/json"
            and not streamed
            and not raw
        ):
            try:
                return result.json()
            except Exception as e:
                raise gitlab.exceptions.GitlabParsingError(
                    error_message="Failed to parse the server message"
                ) from e
        else:
            return result

    def http_list(
        self,
        path: str,
        query_data: Optional[Dict[str, Any]] = None,
        as_list: Optional[bool] = None,
        **kwargs: Any,
    ) -> Union["GitlabList", List[Dict[str, Any]]]:
        """Make a GET request to the Gitlab server for list-oriented queries.

        Args:
            path (str): Path or full URL to query ('/projects' or
                        'http://whatever/v4/api/projects')
            query_data (dict): Data to send as query parameters
            **kwargs: Extra options to send to the server (e.g. sudo, page,
                      per_page)

        Returns:
            list: A list of the objects returned by the server. If `as_list` is
            False and no pagination-related arguments (`page`, `per_page`,
            `all`/`all_pages`) are defined then a GitlabList object (generator) is
            returned instead. This object will make API calls when needed to fetch
            the next items from the server.

        Raises:
            GitlabHttpError: When the return code is not 2xx
            GitlabParsingError: If the json data could not be parsed
        """
        query_data = query_data or {}

        # In case we want to change the default behavior at some point
        as_list = True if as_list is None else as_list

        # Provide an "all_pages" param for endpoints that also take "all" as param.
        get_all = kwargs.pop("all_pages", None)

        if get_all is None:
            get_all = kwargs.pop("all", False)

        url = self._build_url(path)

        page = kwargs.get("page")

        if get_all is True and as_list is True:
            return list(GitlabList(self, url, query_data, **kwargs))

        if page or as_list is True:
            # pagination requested, we return a list
            return list(GitlabList(self, url, query_data, get_next=False, **kwargs))

        # No pagination, generator requested
        return GitlabList(self, url, query_data, **kwargs)

    def http_post(
        self,
        path: str,
        query_data: Optional[Dict[str, Any]] = None,
        post_data: Optional[Dict[str, Any]] = None,
        files: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Union[Dict[str, Any], requests.Response]:
        """Make a POST request to the Gitlab server.

        Args:
            path (str): Path or full URL to query ('/projects' or
                        'http://whatever/v4/api/projecs')
            query_data (dict): Data to send as query parameters
            post_data (dict): Data to send in the body (will be converted to
                              json)
            files (dict): The files to send to the server
            **kwargs: Extra options to send to the server (e.g. sudo)

        Returns:
            The parsed json returned by the server if json is return, else the
            raw content

        Raises:
            GitlabHttpError: When the return code is not 2xx
            GitlabParsingError: If the json data could not be parsed
        """
        query_data = query_data or {}
        post_data = post_data or {}

        result = self.http_request(
            "post",
            path,
            query_data=query_data,
            post_data=post_data,
            files=files,
            **kwargs,
        )
        try:
            if result.headers.get("Content-Type", None) == "application/json":
                return result.json()
        except Exception as e:
            raise gitlab.exceptions.GitlabParsingError(
                error_message="Failed to parse the server message"
            ) from e
        return result

    def http_put(
        self,
        path: str,
        query_data: Optional[Dict[str, Any]] = None,
        post_data: Optional[Dict[str, Any]] = None,
        files: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Union[Dict[str, Any], requests.Response]:
        """Make a PUT request to the Gitlab server.

        Args:
            path (str): Path or full URL to query ('/projects' or
                        'http://whatever/v4/api/projecs')
            query_data (dict): Data to send as query parameters
            post_data (dict): Data to send in the body (will be converted to
                              json)
            files (dict): The files to send to the server
            **kwargs: Extra options to send to the server (e.g. sudo)

        Returns:
            The parsed json returned by the server.

        Raises:
            GitlabHttpError: When the return code is not 2xx
            GitlabParsingError: If the json data could not be parsed
        """
        query_data = query_data or {}
        post_data = post_data or {}

        result = self.http_request(
            "put",
            path,
            query_data=query_data,
            post_data=post_data,
            files=files,
            **kwargs,
        )
        try:
            return result.json()
        except Exception as e:
            raise gitlab.exceptions.GitlabParsingError(
                error_message="Failed to parse the server message"
            ) from e

    def http_delete(self, path: str, **kwargs: Any) -> requests.Response:
        """Make a PUT request to the Gitlab server.

        Args:
            path (str): Path or full URL to query ('/projects' or
                        'http://whatever/v4/api/projecs')
            **kwargs: Extra options to send to the server (e.g. sudo)

        Returns:
            The requests object.

        Raises:
            GitlabHttpError: When the return code is not 2xx
        """
        return self.http_request("delete", path, **kwargs)

    @gitlab.exceptions.on_http_error(gitlab.exceptions.GitlabSearchError)
    def search(
        self, scope: str, search: str, **kwargs: Any
    ) -> Union["GitlabList", List[Dict[str, Any]]]:
        """Search GitLab resources matching the provided string.'

        Args:
            scope (str): Scope of the search
            search (str): Search string
            **kwargs: Extra options to send to the server (e.g. sudo)

        Raises:
            GitlabAuthenticationError: If authentication is not correct
            GitlabSearchError: If the server failed to perform the request

        Returns:
            GitlabList: A list of dicts describing the resources found.
        """
        data = {"scope": scope, "search": search}
        return self.http_list("/search", query_data=data, **kwargs)


class GitlabList(object):
    """Generator representing a list of remote objects.

    The object handles the links returned by a query to the API, and will call
    the API again when needed.
    """

    def __init__(
        self,
        gl: Gitlab,
        url: str,
        query_data: Dict[str, Any],
        get_next: bool = True,
        **kwargs: Any,
    ) -> None:
        self._gl = gl

        # Preserve kwargs for subsequent queries
        self._kwargs = kwargs.copy()

        self._query(url, query_data, **self._kwargs)
        self._get_next = get_next

        # Remove query_parameters from kwargs, which are saved via the `next` URL
        self._kwargs.pop("query_parameters", None)

    def _query(
        self, url: str, query_data: Optional[Dict[str, Any]] = None, **kwargs: Any
    ) -> None:
        query_data = query_data or {}
        result = self._gl.http_request("get", url, query_data=query_data, **kwargs)
        try:
            links = result.links
            if links:
                next_url = links["next"]["url"]
            else:
                next_url = requests.utils.parse_header_links(result.headers["links"])[
                    0
                ]["url"]
            self._next_url = next_url
        except KeyError:
            self._next_url = None
        self._current_page: Optional[Union[str, int]] = result.headers.get("X-Page")
        self._prev_page: Optional[Union[str, int]] = result.headers.get("X-Prev-Page")
        self._next_page: Optional[Union[str, int]] = result.headers.get("X-Next-Page")
        self._per_page: Optional[Union[str, int]] = result.headers.get("X-Per-Page")
        self._total_pages: Optional[Union[str, int]] = result.headers.get(
            "X-Total-Pages"
        )
        self._total: Optional[Union[str, int]] = result.headers.get("X-Total")

        try:
            self._data: List[Dict[str, Any]] = result.json()
        except Exception as e:
            raise gitlab.exceptions.GitlabParsingError(
                error_message="Failed to parse the server message"
            ) from e

        self._current = 0

    @property
    def current_page(self) -> int:
        """The current page number."""
        if TYPE_CHECKING:
            assert self._current_page is not None
        return int(self._current_page)

    @property
    def prev_page(self) -> Optional[int]:
        """The previous page number.

        If None, the current page is the first.
        """
        return int(self._prev_page) if self._prev_page else None

    @property
    def next_page(self) -> Optional[int]:
        """The next page number.

        If None, the current page is the last.
        """
        return int(self._next_page) if self._next_page else None

    @property
    def per_page(self) -> int:
        """The number of items per page."""
        if TYPE_CHECKING:
            assert self._per_page is not None
        return int(self._per_page)

    @property
    def total_pages(self) -> int:
        """The total number of pages."""
        if TYPE_CHECKING:
            assert self._total_pages is not None
        return int(self._total_pages)

    @property
    def total(self) -> int:
        """The total number of items."""
        if TYPE_CHECKING:
            assert self._total is not None
        return int(self._total)

    def __iter__(self) -> "GitlabList":
        return self

    def __len__(self) -> int:
        if self._total is None:
            return 0
        return int(self._total)

    def __next__(self) -> Dict[str, Any]:
        return self.next()

    def next(self) -> Dict[str, Any]:
        try:
            item = self._data[self._current]
            self._current += 1
            return item
        except IndexError:
            pass

        if self._next_url and self._get_next is True:
            self._query(self._next_url, **self._kwargs)
            return self.next()

        raise StopIteration
