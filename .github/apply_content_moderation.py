from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"Expected one match in {path}, found {count}: {old[:80]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "constants.py",
    "DISPLAY_NAME_MIN_CHARS = 3\nDISPLAY_NAME_MAX_CHARS = 18\n\n",
    "DISPLAY_NAME_MIN_CHARS = 3\nDISPLAY_NAME_MAX_CHARS = 18\n\n"
    "# A blocked public submission pauses further profile changes and Spot publishing\n"
    "# for one hour. Claims and financial/cancellation actions remain available.\n"
    "CONTENT_MODERATION_COOLDOWN_SECONDS = 60 * 60\n\n",
)

replace_once(
    "public_html.py",
    "import cache\nimport constants as const\nimport database as schema\n",
    "import cache\nimport constants as const\nimport content_moderation\nimport database as schema\n",
)

replace_once(
    "public_html.py",
    "@router.post(\"/api/my-spots/{spot_id}/publish\")\n"
    "async def my_spots_publish_api(spot_id: int, payload: HomeSessionRequest) -> JSONResponse:\n"
    "    \"\"\"Publish one complete, fully funded draft SPOT.\"\"\"\n"
    "    async with get_db() as db:\n",
    "@router.post(\"/api/my-spots/{spot_id}/publish\")\n"
    "async def my_spots_publish_api(spot_id: int, payload: HomeSessionRequest) -> JSONResponse:\n"
    "    \"\"\"Publish one complete, fully funded draft SPOT.\n\n"
    "    Public text is checked only at this final boundary. Draft editing remains\n"
    "    private and unrestricted; a rude title/description is censored atomically\n"
    "    before the Spot becomes visible.\n"
    "    \"\"\"\n"
    "    moderation_result = {\n"
    "        \"changed\": False,\n"
    "        \"title_changed\": False,\n"
    "        \"description_changed\": False,\n"
    "    }\n"
    "    moderation_marker = None\n\n"
    "    async with get_db() as db:\n",
)

replace_once(
    "public_html.py",
    "            try:\n"
    "                await db_access.publish_spot(db, spot_id=spot_id)\n"
    "            except ValueError as exc:\n"
    "                return JSONResponse({**meta, \"ok\": False, \"code\": \"publish_failed\", \"message\": str(exc)}, status_code=status.HTTP_409_CONFLICT)\n",
    "            checked_at = await db_access.get_unixepoch(db)\n"
    "            active_cooldown = await content_moderation.get_content_cooldown(\n"
    "                db,\n"
    "                user_id=user_id,\n"
    "                checked_at=checked_at,\n"
    "            )\n"
    "            if active_cooldown is not None:\n"
    "                return JSONResponse(\n"
    "                    {\n"
    "                        **meta,\n"
    "                        \"ok\": False,\n"
    "                        \"code\": \"content_moderation_cooldown\",\n"
    "                        \"message\": content_moderation.active_cooldown_message(\n"
    "                            active_cooldown,\n"
    "                            checked_at=checked_at,\n"
    "                        ),\n"
    "                        **content_moderation.cooldown_api_fields(\n"
    "                            active_cooldown,\n"
    "                            checked_at=checked_at,\n"
    "                        ),\n"
    "                    },\n"
    "                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,\n"
    "                )\n\n"
    "            # Avoid censoring or penalising a draft which could not have been\n"
    "            # published for ordinary completeness/funding reasons anyway.\n"
    "            if not await db_access.can_publish_spot(db, spot_id=spot_id):\n"
    "                return JSONResponse(\n"
    "                    {\n"
    "                        **meta,\n"
    "                        \"ok\": False,\n"
    "                        \"code\": \"publish_failed\",\n"
    "                        \"message\": \"spot is not complete and fully funded enough to publish\",\n"
    "                    },\n"
    "                    status_code=status.HTTP_409_CONFLICT,\n"
    "                )\n\n"
    "            moderation_result = await content_moderation.censor_draft_spot_for_publish(\n"
    "                db,\n"
    "                spot_id=spot_id,\n"
    "            )\n\n"
    "            try:\n"
    "                await db_access.publish_spot(db, spot_id=spot_id)\n"
    "            except ValueError as exc:\n"
    "                return JSONResponse({**meta, \"ok\": False, \"code\": \"publish_failed\", \"message\": str(exc)}, status_code=status.HTTP_409_CONFLICT)\n\n"
    "            if moderation_result[\"changed\"]:\n"
    "                moderation_marker = await content_moderation.start_content_cooldown(\n"
    "                    db,\n"
    "                    user_id=user_id,\n"
    "                    reason=\"spot_publish\",\n"
    "                    checked_at=checked_at,\n"
    "                )\n",
)

replace_once(
    "public_html.py",
    "    return JSONResponse(\n"
    "        {\n"
    "            **meta,\n"
    "            \"ok\": True,\n"
    "            \"spot\": _serialise_owner_spot(spot_summary, now=now, transactions=transactions) if spot_summary else None,\n"
    "        }\n"
    "    )\n\n\n"
    "@router.post(\"/api/my-spots/{spot_id}/cancel\")\n",
    "    response = {\n"
    "        **meta,\n"
    "        \"ok\": True,\n"
    "        \"spot\": _serialise_owner_spot(spot_summary, now=now, transactions=transactions) if spot_summary else None,\n"
    "        \"content_censored\": bool(moderation_result[\"changed\"]),\n"
    "        \"title_censored\": bool(moderation_result[\"title_changed\"]),\n"
    "        \"description_censored\": bool(moderation_result[\"description_changed\"]),\n"
    "    }\n"
    "    if moderation_marker is not None:\n"
    "        response.update(content_moderation.cooldown_api_fields(moderation_marker))\n"
    "    return JSONResponse(response)\n\n\n"
    "@router.post(\"/api/my-spots/{spot_id}/cancel\")\n",
)

replace_once(
    "public_html.py",
    "    In normal use the user is identified by the Nimiq Pay device hash. During\n"
    "    desktop development, DEFAULT_TO_TEST_USER lets this endpoint update the\n"
    "    spoof/test user when no device hash is available.\n",
    "    In normal use the user is identified by the Nimiq Pay device hash. During\n"
    "    desktop development, DEFAULT_TO_TEST_USER lets this endpoint update the\n"
    "    spoof/test user when no device hash is available. Blocked words are rejected\n"
    "    on the server so bypassing the page JavaScript cannot save them.\n",
)

replace_once(
    "public_html.py",
    "            if int(user[schema.USER_STATUS]) == const.USER_STATUS_BANNED:\n"
    "                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=\"Banned users cannot edit their profile\")\n\n"
    "            await db_access.modify_user_display_name(db, user_id=user_id, display_name=display_name)\n",
    "            if int(user[schema.USER_STATUS]) == const.USER_STATUS_BANNED:\n"
    "                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=\"Banned users cannot edit their profile\")\n\n"
    "            checked_at = await db_access.get_unixepoch(db)\n"
    "            active_cooldown = await content_moderation.get_content_cooldown(\n"
    "                db,\n"
    "                user_id=user_id,\n"
    "                checked_at=checked_at,\n"
    "            )\n"
    "            if active_cooldown is not None:\n"
    "                return JSONResponse(\n"
    "                    {\n"
    "                        \"ok\": False,\n"
    "                        \"code\": \"content_moderation_cooldown\",\n"
    "                        \"message\": content_moderation.active_cooldown_message(\n"
    "                            active_cooldown,\n"
    "                            checked_at=checked_at,\n"
    "                        ),\n"
    "                        **content_moderation.cooldown_api_fields(\n"
    "                            active_cooldown,\n"
    "                            checked_at=checked_at,\n"
    "                        ),\n"
    "                    },\n"
    "                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,\n"
    "                )\n\n"
    "            if content_moderation.contains_blocked_word(display_name):\n"
    "                moderation_marker = await content_moderation.start_content_cooldown(\n"
    "                    db,\n"
    "                    user_id=user_id,\n"
    "                    reason=\"display_name\",\n"
    "                    checked_at=checked_at,\n"
    "                )\n"
    "                wait = content_moderation.format_wait(\n"
    "                    int(moderation_marker[\"retry_after_seconds\"])\n"
    "                )\n"
    "                return JSONResponse(\n"
    "                    {\n"
    "                        \"ok\": False,\n"
    "                        \"code\": \"inappropriate_display_name\",\n"
    "                        \"message\": (\n"
    "                            \"That display name contains blocked language and was not saved. \"\n"
    "                            \"Public profile changes and Spot publishing are paused for \"\n"
    "                            f\"{wait}.\"\n"
    "                        ),\n"
    "                        **content_moderation.cooldown_api_fields(moderation_marker),\n"
    "                    },\n"
    "                    status_code=status.HTTP_400_BAD_REQUEST,\n"
    "                )\n\n"
    "            await db_access.modify_user_display_name(db, user_id=user_id, display_name=display_name)\n",
)
