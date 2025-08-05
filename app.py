import requests
import time
from bs4 import BeautifulSoup
from flask import Flask, render_template, request, redirect, url_for
import threading
import os

TMDB_API_KEY = os.environ.get('TMDB_API_KEY')

app = Flask(__name__)

def get_user_avatar(session, username):
    try:
        profile_url = f"https://letterboxd.com/{username}/"
        response = session.get(profile_url)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')

        avatar_tag = soup.find('meta', property='og:image')
        if avatar_tag and avatar_tag.get('content'):
            return avatar_tag['content']
    except requests.exceptions.RequestException as e:
        print(f"Could not fetch profile for '{username}': {e}")
    return None

def get_tmdb_details(session, movie_title):
    if not TMDB_API_KEY:
        return {}
    try:
        year = None
        if '(' in movie_title and movie_title.endswith(')'):
            year_str = movie_title[movie_title.rfind('(')+1:-1]
            if year_str.isdigit():
                year = int(year_str)
                movie_title = movie_title[:movie_title.rfind('(')].strip()

        search_url = f"https://api.themoviedb.org/3/search/movie"
        params = {'api_key': TMDB_API_KEY, 'query': movie_title}
        if year:
            params['year'] = year

        response = session.get(search_url, params=params)
        response.raise_for_status()
        search_data = response.json()

        if not search_data['results']:
            return {}

        movie_id = search_data['results'][0]['id']

        details_url = f"https://api.themoviedb.org/3/movie/{movie_id}"
        details_params = {'api_key': TMDB_API_KEY, 'append_to_response': 'credits,images'}
        details_response = session.get(details_url, params=details_params)
        details_response.raise_for_status()
        details_data = details_response.json()

        genres = [genre['name'] for genre in details_data.get('genres', [])]

        director = {'name': 'N/A', 'id': None}
        if 'credits' in details_data:
            for member in details_data['credits']['crew']:
                if member['job'] == 'Director':
                    director = {'name': member['name'], 'id': member['id']}
                    break

        cast = [{'name': actor['name'], 'id': actor['id']} for actor in details_data.get('credits', {}).get('cast', [])[:5]]

        logo_path = ""
        no_lang_logo_path = ""
        if 'images' in details_data and details_data['images']['logos']:
            for logo in details_data['images']['logos']:
                if logo['iso_639_1'] == 'en':
                    logo_path = logo['file_path']
                    break 
                if not no_lang_logo_path and (logo['iso_639_1'] is None or logo['iso_639_1'] == 'xx'):
                    no_lang_logo_path = logo['file_path']

        if not logo_path:
            logo_path = no_lang_logo_path

        return {
            'rating': details_data.get('vote_average', 0.0),
            'poster_path': details_data.get('poster_path', ''),
            'backdrop_path': details_data.get('backdrop_path', ''),
            'logo_path': logo_path,
            'release_date': details_data.get('release_date', ''),
            'runtime': details_data.get('runtime', 0),
            'genres': genres,
            'imdb_id': details_data.get('imdb_id', ''),
            'overview': details_data.get('overview', 'No summary available.'),
            'director': director,
            'cast': cast
        }
    except (requests.exceptions.RequestException, IndexError, KeyError) as e:
        print(f"Could not fetch TMDB details for '{movie_title}': {e}")
    return {}


def get_watchlist(username):
    with requests.Session() as session:
        session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 1.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"})

        movies = set()
        page_url = f"https://letterboxd.com/{username}/watchlist/"

        while page_url:
            try:
                response = session.get(page_url)
                response.raise_for_status()
            except requests.exceptions.RequestException:
                return None

            soup = BeautifulSoup(response.text, 'html.parser')
            film_items = soup.find_all('li', class_='poster-container')
            if not film_items:
                break

            for item in film_items:
                poster_div = item.find('div', class_='film-poster')
                img = poster_div.find('img')
                title = img['alt'] if img else 'Unknown Title'

                relative_url = poster_div.get('data-target-link', '#')
                full_url = f"https://letterboxd.com{relative_url}"

                movies.add((title, full_url))

            next_button = soup.find('a', class_='next')
            if next_button and next_button.has_attr('href'):
                page_url = "https://letterboxd.com" + next_button['href']
                time.sleep(0.5) 
            else:
                page_url = None

    return movies

def scrape_and_store(username, results_dict):
    watchlist = get_watchlist(username)
    results_dict[username] = watchlist

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        usernames_raw = request.form.get('usernames')
        usernames = [name.strip() for name in usernames_raw.split() if name.strip()]

        # Redirect to the results page with usernames as query parameters
        return redirect(url_for('results', users=','.join(usernames)))

    return render_template('index.html')

@app.route('/results')
def results():
    usernames_str = request.args.get('users', '')
    usernames = list(set(usernames_str.split(','))) if usernames_str else []

    if not usernames:
        return redirect(url_for('index'))

    user_avatars = {}
    with requests.Session() as session:
        for user in usernames:
            avatar_url = get_user_avatar(session, user)
            if not avatar_url:
                 error_msg = f"Could not find profile for user: '{user}'. The profile may be private or the username is incorrect."
                 return render_template('index.html', error=error_msg)
            user_avatars[user] = avatar_url

    threads = []
    scraped_data = {}

    for user in usernames:
        thread = threading.Thread(target=scrape_and_store, args=(user, scraped_data))
        threads.append(thread)
        thread.start()

    for thread in threads:
        thread.join()

    valid_users = []
    for user in usernames:
        if scraped_data.get(user):
            valid_users.append(user)
        else:
            error_msg = f"Could not find a public watchlist for user: '{user}'. The profile may be private, empty, or the username is incorrect."
            return render_template('index.html', error=error_msg)

    movie_counts = {}
    for user, watchlist in scraped_data.items():
        if watchlist:
            for movie_title, movie_url in watchlist:
                if movie_title not in movie_counts:
                    movie_counts[movie_title] = {'users': [], 'url': movie_url}
                movie_counts[movie_title]['users'].append(user)

    processed_movies = []
    with requests.Session() as tmdb_session:
        for title, data in movie_counts.items():
            if len(data['users']) >= 2:
                tmdb_details = get_tmdb_details(tmdb_session, title)

                rating = tmdb_details.get('rating', 0.0)
                runtime = tmdb_details.get('runtime', 0)
                synopsis = tmdb_details.get('overview', '').strip()
                if not synopsis:
                    synopsis = "No overview available."

                poster_url = f"https://image.tmdb.org/t/p/w500{tmdb_details.get('poster_path')}" if tmdb_details.get('poster_path') else "https://placehold.co/500x750/2c2f33/e94b3c?text=No+Poster"
                backdrop_url = f"https://image.tmdb.org/t/p/original{tmdb_details.get('backdrop_path')}" if tmdb_details.get('backdrop_path') else poster_url
                logo_url = f"https://image.tmdb.org/t/p/original{tmdb_details.get('logo_path')}" if tmdb_details.get('logo_path') else ""

                user_objects = [{'name': user, 'avatar': user_avatars.get(user)} for user in sorted(data['users'])]

                processed_movies.append({
                    'title': title,
                    'url': data['url'],
                    'rating': rating,
                    'formatted_rating': f"{rating:.1f}" if rating > 0 else "N/A",
                    'poster_url': poster_url,
                    'backdrop_url': backdrop_url,
                    'logo_url': logo_url,
                    'release_date': tmdb_details.get('release_date', '0000-00-00'),
                    'release_year': tmdb_details.get('release_date', '----').split('-')[0],
                    'runtime': runtime,
                    'formatted_runtime': f"{runtime} min" if runtime > 0 else "",
                    'genres': tmdb_details.get('genres', []),
                    'imdb_id': tmdb_details.get('imdb_id', ''),
                    'overview': synopsis,
                    'director': tmdb_details.get('director', {'name': 'N/A', 'id': None}),
                    'cast': tmdb_details.get('cast', []),
                    'users': user_objects,
                    'count': len(data['users'])
                })

    sorted_movies = sorted(processed_movies, key=lambda x: (x['count'], x['rating']), reverse=True)

    return render_template('index.html', movies=sorted_movies, users=valid_users)
