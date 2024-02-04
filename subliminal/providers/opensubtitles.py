# -*- coding: utf-8 -*-
import logging
import os
import re

from babelfish import Language, language_converters
from guessit import guessit
from iso639 import languages as langs
from opensubtitlescom import OpenSubtitles

from . import Provider
from .. import __short_version__
from ..exceptions import (ConfigurationError)
from ..matches import guess_matches
from ..subtitle import Subtitle, fix_line_ending
from ..video import Episode, Movie

logger = logging.getLogger(__name__)


class OpenSubtitlesSubtitle(Subtitle):
    """OpenSubtitles Subtitle."""
    provider_name = 'opensubtitles'
    series_re = re.compile(r'^"(?P<series_name>.*)" (?P<series_title>.*)$')

    def __init__(self, language, hearing_impaired, page_link, subtitle_id, matched_by, movie_kind, hash, movie_name,
                 movie_release_name, movie_year, movie_imdb_id, series_season, series_episode, filename, encoding):
        super(OpenSubtitlesSubtitle, self).__init__(language, hearing_impaired=hearing_impaired,
                                                    page_link=page_link, encoding=encoding)
        self.subtitle_id = subtitle_id
        self.matched_by = matched_by
        self.movie_kind = movie_kind
        self.hash = hash
        self.movie_name = movie_name
        self.movie_release_name = movie_release_name
        self.movie_year = movie_year
        self.movie_imdb_id = movie_imdb_id
        self.series_season = series_season
        self.series_episode = series_episode
        self.filename = filename

    @property
    def id(self):
        return str(self.subtitle_id)

    @property
    def info(self):
        if not self.filename and not self.movie_release_name:
            return self.subtitle_id
        if self.movie_release_name and len(self.movie_release_name) > len(self.filename):
            return self.movie_release_name
        return self.filename

    @property
    def series_name(self):
        return self.series_re.match(self.movie_name).group('series_name')

    @property
    def series_title(self):
        return self.series_re.match(self.movie_name).group('series_title')

    def get_matches(self, video):
        if (isinstance(video, Episode) and self.movie_kind != 'episode') or (
                isinstance(video, Movie) and self.movie_kind != 'movie'):
            logger.info('%r is not a valid movie_kind', self.movie_kind)
            return set()

        matches = guess_matches(video, {
            'title': self.series_name if self.movie_kind == 'episode' else self.movie_name,
            'episode_title': self.series_title if self.movie_kind == 'episode' else None,
            'year': self.movie_year,
            'season': self.series_season,
            'episode': self.series_episode
        })

        # tag
        if self.matched_by == 'tag':
            if not video.imdb_id or self.movie_imdb_id == video.imdb_id:
                if self.movie_kind == 'episode':
                    matches |= {'series', 'year', 'season', 'episode'}
                elif self.movie_kind == 'movie':
                    matches |= {'title', 'year'}

        # guess
        matches |= guess_matches(video, guessit(self.movie_release_name, {'type': self.movie_kind}))
        matches |= guess_matches(video, guessit(self.filename, {'type': self.movie_kind}))

        # hash
        if 'opensubtitles' in video.hashes and self.hash == video.hashes['opensubtitles']:
            if self.movie_kind == 'movie' and 'title' in matches:
                matches.add('hash')
            elif self.movie_kind == 'episode' and 'series' in matches and 'season' in matches and 'episode' in matches:
                matches.add('hash')
            else:
                logger.debug('Match on hash discarded')

        # imdb_id
        if video.imdb_id and self.movie_imdb_id == video.imdb_id:
            matches.add('imdb_id')

        return matches


def convert_language_code(three_letter_code):
    language = langs.get(part3=three_letter_code)
    return language.alpha2


class OpenSubtitlesProvider(Provider):
    """OpenSubtitles Provider.

    :param str username: username.
    :param str password: password.

    """
    languages = {Language.fromopensubtitles(l) for l in language_converters['opensubtitles'].codes}
    server_url = 'https://api.opensubtitles.org/xml-rpc'
    subtitle_class = OpenSubtitlesSubtitle
    user_agent = 'subliminal v%s' % __short_version__

    def __init__(self, username=None, password=None, api_key=None, app_name=None):
        if any((username, password, api_key)) and not all((username, password, api_key)):
            raise ConfigurationError('Username, password and api key must be specified')
        # None values not allowed for logging in, so replace it by ''
        self.username = username or ''
        self.password = password or ''
        self.api_key = api_key or ''
        self.api = OpenSubtitles(app_name, api_key) or None

    def initialize(self):
        logger.info('Logging in')
        self.api.login(self.username, self.password)
        logger.debug('Logged in')

    def terminate(self):
        logger.info('Logging out')
        self.api.logout(self.username, self.password)
        logger.debug('Logged out')

    def query(self, wanted_languages, hash=None, size=None, imdb_id=None, query=None, season=None, episode=None,
              tag=None):

        # query the server
        logger.info('Searching subtitles')
        response = self.api.search(episode_number=episode, moviehash=hash, query=query, season_number=season,
                                   imdb_id=imdb_id, languages=','.join(sorted(
                convert_language_code(lang.opensubtitles) for lang in sorted(wanted_languages))))
        subtitles = []

        # exit if no data
        if not response.data:
            logger.debug('No subtitles found')
            return subtitles

        # loop over subtitle items
        for subtitle_item in response.data:
            # read the item
            language = Language.fromopensubtitles(subtitle_item.language)
            hearing_impaired = subtitle_item.hearing_impaired
            subtitle_id = subtitle_item.file_id
            movie_name = subtitle_item.movie_name
            movie_release_name = subtitle_item.release
            movie_year = subtitle_item.year
            movie_imdb_id = 'tt' + str(subtitle_item.imdb_id)
            series_season = subtitle_item.season_number
            series_episode = subtitle_item.episode_number
            encoding = "UTF-8"

            subtitle = self.subtitle_class(language, hearing_impaired, None, subtitle_id, None, None,
                                           hash, movie_name, movie_release_name, movie_year, movie_imdb_id,
                                           series_season, series_episode, None, encoding)
            logger.debug('Found subtitle %r', subtitle)
            subtitles.append(subtitle)

        return subtitles

    def list_subtitles(self, video, languages):
        season = episode = None
        if isinstance(video, Episode):
            query = video.series
            season = video.season
            episode = video.episode
        else:
            query = video.title

        return self.query(languages, hash=video.hashes.get('opensubtitles'), size=video.size, imdb_id=video.imdb_id,
                          query=query, season=season, episode=episode, tag=os.path.basename(video.name))

    def download_subtitle(self, subtitle):
        logger.info('Downloading subtitle %r', subtitle)
        response = self.api.download(file_id=subtitle.subtitle_id)
        subtitle.content = fix_line_ending(response)
