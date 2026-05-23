# SkySkimmer ✈️

A lightweight, cloud-native flight pricing engine for long-running, scheduled evaluation of highly specific travel itineraries.

## Overview

SkySkimmer tracks custom itineraries — specific stopovers, alliance routing, points-to-cash valuation — and fires alerts via Discord/Slack when meaningful price drops are detected.

## Architecture

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full system blueprint.

## Tech Stack

- **Language**: Python 3.11+
- **Host**: Railway (containerized) or AWS Lambda
- **Data**: Tequila by Kiwi API (primary)
- **State**: Supabase (production) / JSON file (local dev)
- **Notifications**: Discord Webhooks
- **Scheduler**: APScheduler

## Status

🏗️ Phase 1 — Architecture Blueprint complete.
