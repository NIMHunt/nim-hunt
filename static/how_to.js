const platformTabs = [...document.querySelectorAll('[data-how-to-platform]')];
const permissionPanels = [...document.querySelectorAll('[data-how-to-permission]')];
const findSpotsImage = document.querySelector('[data-how-to-find-spots-image]');
const findSpotsImageError = document.querySelector('[data-how-to-image-error]');

function selectPlatform(platform, { focus = false } = {}) {
    for (const tab of platformTabs) {
        const selected = tab.dataset.howToPlatform === platform;
        tab.classList.toggle('is-selected', selected);
        tab.setAttribute('aria-selected', selected ? 'true' : 'false');
        tab.tabIndex = selected ? 0 : -1;
        if (selected && focus) tab.focus();
    }

    for (const panel of permissionPanels) {
        panel.hidden = panel.dataset.howToPermission !== platform;
    }
}

function movePlatformSelection(currentTab, direction) {
    const currentIndex = platformTabs.indexOf(currentTab);
    if (currentIndex < 0 || platformTabs.length === 0) return;

    const nextIndex = (currentIndex + direction + platformTabs.length) % platformTabs.length;
    selectPlatform(platformTabs[nextIndex].dataset.howToPlatform, { focus: true });
}

function showFindSpotsImageError() {
    if (findSpotsImage) findSpotsImage.hidden = true;
    if (findSpotsImageError) findSpotsImageError.hidden = false;
}

async function loadFindSpotsImage() {
    if (!findSpotsImage) return;

    const prefix = findSpotsImage.dataset.howToImageChunkPrefix;
    if (!prefix) {
        showFindSpotsImageError();
        return;
    }

    const chunkUrls = [1, 2, 3, 4].map(
        (number) => `${prefix}${number}.b64?v=how-to-find-spots-v2-20260729`,
    );
    const chunks = await Promise.all(chunkUrls.map(async (url) => {
        const response = await fetch(url, { cache: 'force-cache' });
        if (!response.ok) throw new Error(`Could not load ${url}`);
        return (await response.text()).trim();
    }));

    findSpotsImage.addEventListener('load', () => {
        findSpotsImage.hidden = false;
        if (findSpotsImageError) findSpotsImageError.hidden = true;
    }, { once: true });
    findSpotsImage.addEventListener('error', showFindSpotsImageError, { once: true });
    findSpotsImage.src = `data:image/webp;base64,${chunks.join('')}`;
}

for (const tab of platformTabs) {
    tab.addEventListener('click', () => {
        selectPlatform(tab.dataset.howToPlatform);
    });

    tab.addEventListener('keydown', (event) => {
        if (event.key === 'ArrowLeft') {
            event.preventDefault();
            movePlatformSelection(tab, -1);
        } else if (event.key === 'ArrowRight') {
            event.preventDefault();
            movePlatformSelection(tab, 1);
        } else if (event.key === 'Home') {
            event.preventDefault();
            selectPlatform(platformTabs[0]?.dataset.howToPlatform, { focus: true });
        } else if (event.key === 'End') {
            event.preventDefault();
            selectPlatform(platformTabs.at(-1)?.dataset.howToPlatform, { focus: true });
        }
    });
}

if (platformTabs.length > 0 && permissionPanels.length > 0) {
    const initialPlatform = /Android/i.test(navigator.userAgent) ? 'android' : 'ios';
    selectPlatform(initialPlatform);
}

loadFindSpotsImage().catch((error) => {
    console.error(error);
    showFindSpotsImageError();
});