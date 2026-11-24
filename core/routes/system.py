"""Reachability checks and conversation starters."""
from datetime import datetime
from flask import Blueprint, current_app, jsonify, request
from core import fels_suite, voice_service
from core.health import HealthService
from core.remote_access import direct_local_request
from core.version import SUITE_API, VERSION

bp = Blueprint('system', __name__)


@bp.get('/api/health')
def suite_health():
    return jsonify(ok=True, fels=fels_suite.health_block('altera', VERSION, SUITE_API)), 200, {'Cache-Control': 'no-store'}


@bp.get('/api/gpu')
def suite_gpu_status():
    status = fels_suite.gpu_status()
    holders = [{**{key: holder.get(key) for key in ('what', 'since', 'alone')},
                'name': fels_suite.short_name(holder['app'])} for holder in status['holders']]
    alone = next((holder for holder in holders if holder['alone']), None)
    pending = status['alone']
    waiting = None if alone or not pending else {
        'name': fels_suite.short_name(pending['app']), 'what': pending.get('what'), 'since': pending.get('since')}
    return jsonify(state='alone' if alone else 'shared' if holders else 'free',
                   holders=holders, waiting=waiting), 200, {'Cache-Control': 'no-store'}


@bp.post('/api/unload')
def suite_unload():
    headers = request.headers
    if (not direct_local_request() or 'Origin' in headers or 'Referer' in headers
            or any(name.lower().startswith('sec-fetch-') for name in headers.keys())):
        return jsonify(error='Only local apps can ask Altera to free the GPU.', code='local_only'), 403
    if not request.is_json:
        return jsonify(error='Send a JSON request.', code='json_required'), 415
    doing = voice_service.unload_gpu()
    if doing:
        return jsonify(error=f'Altera is {doing}. Try again when it is done.', code='busy', doing=doing), 409
    return jsonify(ok=True)


@bp.get('/api/health/details')
def system_health_details():
    service = current_app.extensions.setdefault('altera_health', HealthService())
    return jsonify(service.check(request.args.get('session_id', type=int))), 200, {'Cache-Control': 'no-store'}


@bp.get('/api/diagnostics')
def diagnostics():
    from core.datastore import get_datastore
    from core.diagnostics import snapshot
    session_id = request.args.get('session_id', type=int)
    if not session_id or session_id < 1:
        return jsonify(error='Choose a conversation to inspect diagnostics.'), 400
    data = snapshot(get_datastore(), session_id)
    if data is None:
        return jsonify(error='Conversation not found.'), 404
    return jsonify(data), 200, {'Cache-Control': 'no-store'}


CONVERSATION_STARTERS = {
    "morning": [
        "What are your plans for today?",
        "How did you sleep? 😴",
        "Got any goals for the day ahead?",
        "What's one thing you're looking forward to?"
    ],
    "afternoon": [
        "How's your day going so far?",
        "Need a break from whatever you're working on?",
        "Want to hear something interesting?",
        "What's the best part of your afternoon been?"
    ],
    "evening": [
        "How was your day overall?",
        "Anything exciting happen today?",
        "Ready to unwind? I've got stories.",
        "What would make tonight a good night?"
    ],
    "night": [
        "Still up? What's on your mind?",
        "Want to reflect on today before sleeping?",
        "Got any thoughts you want to get out there?",
        "Time to wind down together?"
    ]
}

GENERAL_STARTERS = [
    "Tell me about something that made you smile recently 😊",
    "If you could teleport anywhere right now, where would you go?",
    "What's a skill you've always wanted to learn?",
    "Want to play a quick word game? I'll start!",
    "Have you read or watched anything good lately?",
    "What's something you're curious about right now?",
    "If we could talk about any topic, what would be most fun?",
    "Tell me about your favorite place and why it matters to you"
]


@bp.route("/api/conversation-starters", methods=["GET"])
def get_conversation_starters():
    """Get suggested conversation starters based on time of day and user interests."""
    from core.profile import load_user_profile

    profile = load_user_profile()
    name = profile.get("name", "") or ""
    hobbies = profile.get("hobbies", []) or []

    # Determine time period
    hour = datetime.now().hour
    if 5 <= hour < 12:
        period = "morning"
    elif 12 <= hour < 17:
        period = "afternoon"
    elif 17 <= hour < 22:
        period = "evening"
    else:
        period = "night"

    # Get time-specific starters
    starters = list(CONVERSATION_STARTERS.get(period, GENERAL_STARTERS))

    # Add interest-aware starter if user has interests
    if hobbies and len(hobbies) > 0:
        interest = hobbies[0] if isinstance(hobbies, list) else "general"
        starters.insert(0, f"I'd love to hear more about your work with {interest} — what's new?")

    # Add personalized starter if we know the name
    if name:
        starters.append(f"{name}, want to tell me how you're really doing today?")

    return jsonify({"starters": starters, "period": period})
