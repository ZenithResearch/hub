# Feeds Service

Feeds polls configured RSS/Atom sources and emits normalized updates into Hub event/queue surfaces.

## Runtime entrypoint

- Compose service: `feeds`
- Source package: `services/feeds/`
- Entrypoint: `python -m services.feeds.main`

## Connected services

- Eventbus for feed-update events.
- Queue HTTP when feed items should become work.
- `feeds_data` volume for local service state.

## Main source files

- `main.py` — polling service loop.
- `fetcher.py` — feed fetching/parsing.
- `models.py` — feed item models.

## Current docs

- Root README “Service map”.
- `../../docs/README.md` for broader docs navigation.
