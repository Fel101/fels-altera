"""Page-rendering routes extracted from altera.py (Session 6: Blueprints)."""

from flask import Blueprint, current_app, redirect, render_template, request, send_from_directory, session, url_for

from core.config import get_config, get_personality
from core.datastore import get_datastore
from core.version import VERSION

bp = Blueprint('pages', __name__)


@bp.app_context_processor
def dataset_context():
    if request.endpoint == 'backups.recovery_page':
        return {"dataset_epoch": None, "altera_version": VERSION}
    return {"dataset_epoch": get_datastore().dataset_epoch()["value"], "altera_version": VERSION}


@bp.after_request
def private_pages(response):
    if response.mimetype == 'text/html':
        response.headers['Cache-Control'] = 'no-store'
    return response


@bp.route("/sw.js")
def service_worker():
    """A worker at the root can control the chat and the other app pages."""
    response = send_from_directory(current_app.static_folder, "sw.js", mimetype="application/javascript", max_age=0)
    response.headers["Cache-Control"] = "no-cache"
    return response


@bp.route("/")
def index():
    """Main chat interface. It opens on the companion you talked to last; only with no avatar at all does the default chat
    (kept in the browser session) speak.

    In the kids view it opens on a kids pal and nothing else: the server refuses every other person for that browser."""
    from core import addons, adult, lock
    kids_view = lock.in_kids_view()
    try:
        from core import companions
        companions.migrate_legacy()
        companions.migrate_kinds()
        if kids_view:
            opened = lock.ensure_kids_active().get("session_id")
        else:
            opened = companions.current().get("session_id")
    except Exception:
        opened = None
    if opened:
        session['session_id'] = opened
    elif not get_datastore().get_session(session.get('session_id')):
        store = get_datastore()
        available = store.list_sessions(archived=False)
        session['session_id'] = available[0].id if available else store.create_session("Default Chat")
    return render_template(
        "index.html",
        session_id=session['session_id'],
        dataset_epoch=get_datastore().dataset_epoch()["value"],
        personality=get_personality(),
        config=get_config(),
        kids_view=kids_view,
        starter_chat=not kids_view and bool(opened) and request.args.get("starter") == "1",
        addon_panels=[] if kids_view else addons.panels(),
        addon_notice="" if kids_view else adult.startup_notice(),
    )


@bp.get("/setup")
def setup_page():
    """The first run: a name, one tiny model check, and which optional pieces are up. It ends on Home.

    Also reachable from Settings as "Setup check", with `?check=1`, which changes only the wording and where Done goes.
    """
    return render_template("setup.html", personality=get_personality())


@bp.get("/home")
def home_page():
    """The start screen: Pals, Characters and Companions, the person to continue with, and everyone in one gallery.
    The page reads /api/people; opening someone goes back to the chat at /.

    A first run has not chosen a model yet, so it is sent to /setup first rather than shown an empty start screen."""
    from core.profile import is_onboarding_complete
    from core import adult
    if not is_onboarding_complete():
        return redirect(url_for("pages.setup_page"))
    return render_template("home.html", personality=get_personality(), addon_notice=adult.startup_notice())


@bp.route("/memory")
def memory_page():
    """Memory management page."""
    return render_template('memory.html', personality=get_personality())


@bp.route("/timeline")
def timeline_page():
    """Timeline view of conversations with mood annotations."""
    if 'session_id' not in session:
        store = get_datastore()
        session['session_id'] = store.create_session("Default Chat")

    return render_template(
        "timeline.html",
        personality=get_personality(),
        config=get_config()
    )


@bp.get('/story')
def story_page():
    """Source-backed episodes and explicit reflection controls; no inference."""
    return render_template('story.html', personality=get_personality())


@bp.route("/search")
def search_page():
    """Render the conversation search page."""
    return render_template("search.html", personality=get_personality(), config=get_config())


@bp.route("/knowledge")
def knowledge_page():
    """Render the knowledge summary / life data page."""
    return render_template("knowledge.html", personality=get_personality(), config=get_config())
