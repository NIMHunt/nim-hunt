    "static/my_spots.js",
    "    expandedSpotIds: new Set(),\n",
    "    expandedSpotIds: new Set(),\n    expandedClaimCodeSpotIds: new Set(),\n",
)
replace_once(
    "static/my_spots.js",
    "} from './spot_ui.js?v=qol-v1-20260717';",
    "} from './spot_ui.js?v=polish-live-v1-20260720';",
)
replace_once(
    "static/my_spots.js",
    "    if (spot.status_label === 'draft') {\n        if (spot.badge_status_label === 'deposited') return fragment;",
    "    if (spot.status_label === 'draft') {\n        if (['depositing', 'deposited'].includes(spot.badge_status_label)) return fragment;",
)
replace_once(
    "static/my_spots.js",
    "        const claimCodesControl = createOwnerClaimCodesControl();\n",
    _indent_block(dedent("""\
    const spotId = Number(spot.id);
    const claimCodesControl = createOwnerClaimCodesControl({}, {
        expanded: state.expandedClaimCodeSpotIds.has(spotId),
        onToggle: (expanded) => {
            if (expanded) state.expandedClaimCodeSpotIds.add(spotId);
            else state.expandedClaimCodeSpotIds.delete(spotId);
        },
    });
    """), 8),
)
replace_once(
    "static/my_spots.js",
    dedent("""\
    function renderSection(bucket, spots) {
        const copy = TEXT.sections[bucket];
        const section = document.createElement('section');
        section.className = `spot-list-card my-spots-section-card is-${bucket}`;
        section.setAttribute('aria-label', copy.title);

        const toggle = document.createElement('button');
        toggle.type = 'button';
        toggle.className = 'spot-section-toggle disclosure-toggle';
        toggle.setAttribute('aria-expanded', state.sectionExpanded[bucket] ? 'true' : 'false');

        const title = document.createElement('span');
        title.textContent = `${copy.title} (${spots.length})`;
        toggle.append(title);

        const list = document.createElement('ol');
        list.className = 'spot-list';
        list.hidden = !state.sectionExpanded[bucket];

        const empty = document.createElement('p');
        empty.className = 'empty-spots';
        empty.textContent = copy.empty;
        empty.hidden = !state.sectionExpanded[bucket] || spots.length > 0;

        toggle.addEventListener('click', () => {
            state.sectionExpanded[bucket] = toggle.getAttribute('aria-expanded') !== 'true';
            toggle.setAttribute('aria-expanded', state.sectionExpanded[bucket] ? 'true' : 'false');
            list.hidden = !state.sectionExpanded[bucket];
            empty.hidden = !state.sectionExpanded[bucket] || spots.length > 0;
        });

        for (const spot of spots) {
            list.append(createSpotListItem({
                spot,
                detailBuilder: buildMySpotDetail,
                metaBuilder: buildMySpotMeta,
                expanded: state.expandedSpotIds.has(Number(spot.id)),
                onToggle: (spotId, expanded) => {
                    if (expanded) {
                        state.expandedSpotIds.add(spotId);
                    } else {
                        state.expandedSpotIds.delete(spotId);
                    }
                },
            }));
        }

        section.append(toggle, list, empty);
        return section;
    }

    function renderSpots(spots) {
        els.sections.replaceChildren();
        els.empty.hidden = true;

        if (spots.length === 0) {
            renderEmptyAll();
            return;
        }

        const groups = groupSpots(spots);
        els.sections.append(
            renderSection('active', groups.active),
            renderSection('upcoming', groups.upcoming),
            renderSection('draft', groups.draft),
            renderSection('previous', groups.previous)
        );
    }
    """),
    dedent("""\
    function mySpotRenderSignature(spot) {
        return JSON.stringify(spot);
    }

    function buildMySpotListItem(spot) {
        const item = createSpotListItem({
            spot,
            detailBuilder: buildMySpotDetail,
            metaBuilder: buildMySpotMeta,
            expanded: state.expandedSpotIds.has(Number(spot.id)),
            onToggle: (spotId, expanded) => {
                if (expanded) state.expandedSpotIds.add(spotId);
                else state.expandedSpotIds.delete(spotId);
            },
        });
        item.dataset.spotId = String(Number(spot.id));
        item.dataset.renderSignature = mySpotRenderSignature(spot);
        return item;
    }

    function renderSection(bucket, spots) {
        const copy = TEXT.sections[bucket];
        const section = document.createElement('section');
        section.className = `spot-list-card my-spots-section-card is-${bucket}`;
        section.dataset.bucket = bucket;
        section.setAttribute('aria-label', copy.title);

        const toggle = document.createElement('button');
        toggle.type = 'button';
        toggle.className = 'spot-section-toggle disclosure-toggle';
        toggle.setAttribute('aria-expanded', state.sectionExpanded[bucket] ? 'true' : 'false');

        const title = document.createElement('span');
        title.dataset.sectionTitle = 'true';
        title.textContent = `${copy.title} (${spots.length})`;
        toggle.append(title);

        const list = document.createElement('ol');
        list.className = 'spot-list';
        list.dataset.sectionList = 'true';
        list.hidden = !state.sectionExpanded[bucket];

        const empty = document.createElement('p');
        empty.className = 'empty-spots';
        empty.dataset.sectionEmpty = 'true';
        empty.textContent = copy.empty;
        empty.hidden = !state.sectionExpanded[bucket] || spots.length > 0;

        toggle.addEventListener('click', () => {
            state.sectionExpanded[bucket] = toggle.getAttribute('aria-expanded') !== 'true';
            toggle.setAttribute('aria-expanded', state.sectionExpanded[bucket] ? 'true' : 'false');
            list.hidden = !state.sectionExpanded[bucket];
            empty.hidden = !state.sectionExpanded[bucket] || spots.length > 0;
        });

        for (const spot of spots) list.append(buildMySpotListItem(spot));
        section.append(toggle, list, empty);
        return section;
    }

    function reconcileSection(bucket, spots) {
        const section = els.sections.querySelector(`[data-bucket="${bucket}"]`);
        if (!section) {
            els.sections.append(renderSection(bucket, spots));
            return;
        }

        const copy = TEXT.sections[bucket];
        const title = section.querySelector('[data-section-title="true"]');
        const list = section.querySelector('[data-section-list="true"]');
        const empty = section.querySelector('[data-section-empty="true"]');
        if (!title || !list || !empty) {
            section.replaceWith(renderSection(bucket, spots));
            return;
        }

        title.textContent = `${copy.title} (${spots.length})`;
        const existing = new Map(
            [...list.children].map((item) => [Number(item.dataset.spotId), item]),
        );
        const desired = [];
        for (const spot of spots) {
            const spotId = Number(spot.id);
            const signature = mySpotRenderSignature(spot);
            const current = existing.get(spotId);
            if (current?.dataset.renderSignature === signature) desired.push(current);
            else desired.push(buildMySpotListItem(spot));
            existing.delete(spotId);
        }

