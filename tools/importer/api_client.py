"""Client ThemeParks.wiki : endpoints « children » (arborescence) et « live » (statuts, horaires de shows)."""
import re
import requests

API = 'https://api.themeparks.wiki/v1'
UUID_RE = re.compile(r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}')
TYPES = {'ATTRACTION': 'attractions', 'SHOW': 'shows', 'DINING': 'restaurants', 'RESTAURANT': 'restaurants'}


def extract_id(value):
    """Accepte un UUID, une URL (…/entity/<id>/children) ou un slug ; renvoie l'identifiant à interroger."""
    value = (value or '').strip()
    m = UUID_RE.search(value)
    if m:
        return m.group(0).lower()
    m = re.search(r'/entity/([^/?#]+)', value)
    return m.group(1) if m else value


def _get_json(url, timeout=60):
    r = requests.get(url, timeout=timeout, headers={'User-Agent': 'theme-parks-companion-builder'})
    r.raise_for_status()
    return r.json()


def fetch_entities(children_ref, live_ref, log=print, get_json=_get_json):
    """Croise « children » et « live ». Renvoie (liste d'entités, nom de la destination, fuseau, infos).

    infos : identifiants themeparks.wiki de la destination et de ses parcs (pour parks.json).

    Chaque entité : id, name, kind (attractions/shows/restaurants), externalId, parentId, lat, lng,
    status, showtimes (liste « HH:MM »), live (bool : présente dans l'API live).
    """
    cid, lid = extract_id(children_ref), extract_id(live_ref) or extract_id(children_ref)
    try:
        children = get_json(f'{API}/entity/{cid}/children')
    except Exception as e:
        raise RuntimeError(f"API children injoignable ({e})")
    live = {}
    try:
        for x in get_json(f'{API}/entity/{lid}/live').get('liveData', []):
            live[x['id']] = x
    except Exception as e:
        log(f"! API live indisponible ({e}) : statuts et horaires de l'API ignorés")
    ents = []
    for c in children.get('children', []):
        kind = TYPES.get(c.get('entityType'))
        if not kind:
            continue
        lv = live.get(c['id'], {})
        loc = c.get('location') or {}
        ents.append(dict(
            id=c['id'], name=c.get('name', ''), kind=kind, externalId=c.get('externalId') or '',
            parentId=c.get('parentId'), lat=loc.get('latitude', ''), lng=loc.get('longitude', ''),
            status=lv.get('status', ''), live=c['id'] in live, showtimes=_showtimes(lv, children.get('timezone')),
            operating=_operating(lv, children.get('timezone'))))
    log(f"API : {len(ents)} entités ({sum(e['live'] for e in ents)} avec données live)")
    parks = [dict(id=c['id'], name=c.get('name', ''), lat=(c.get('location') or {}).get('latitude', ''),
                  lng=(c.get('location') or {}).get('longitude', ''), type=c.get('entityType'))
             for c in children.get('children', []) if c.get('entityType') == 'PARK']
    info = dict(destination_id=children.get('id', cid), destination_name=children.get('name', ''), entity_type=children.get('entityType', ''),
                timezone=children.get('timezone', ''), parks=parks, children_url=f'{API}/entity/{cid}/children', live_url=f'{API}/entity/{lid}/live')
    return ents, children.get('name', ''), children.get('timezone'), info


def _fmt(iso, tz):
    """'2026-11-04T14:30:00+01:00' -> '14:30' (heure locale du parc quand zoneinfo est disponible)."""
    try:
        from datetime import datetime
        d = datetime.fromisoformat(iso.replace('Z', '+00:00'))
        if tz:
            from zoneinfo import ZoneInfo
            d = d.astimezone(ZoneInfo(tz))
        return d.strftime('%H:%M')
    except Exception:
        return ''


def _showtimes(lv, tz):
    out = []
    for s in lv.get('showtimes') or []:
        t = _fmt(s.get('startTime', ''), tz)
        if t:
            e = _fmt(s.get('endTime', ''), tz) if s.get('endTime') else ''
            out.append(f'{t}-{e}' if e and e != t else t)
    return out


def _operating(lv, tz):
    """Plage d'ouverture du jour si l'API live la fournit (operatingHours)."""
    for o in lv.get('operatingHours') or []:
        a, b = _fmt(o.get('startTime', ''), tz), _fmt(o.get('endTime', ''), tz)
        if a and b:
            return f'{a}-{b}'
    return ''


def park_names(children_ref, get_json=_get_json):
    """{parentId: nom du parc} pour étiqueter la colonne « Parc » (échec silencieux)."""
    out = {}
    try:
        cid = extract_id(children_ref)
        dest = get_json(f'{API}/entity/{cid}')
        out[dest.get('id', cid)] = dest.get('name', '')
    except Exception:
        pass
    return out


def resolve_parent_names(ents, get_json=_get_json):
    """Nom lisible de chaque parentId (un appel par parc distinct)."""
    names = {}
    for pid in {e['parentId'] for e in ents if e['parentId']}:
        try:
            names[pid] = get_json(f'{API}/entity/{pid}').get('name', '')
        except Exception:
            names[pid] = ''
    return names
