const platformTabs = [...document.querySelectorAll('[data-how-to-platform]')];
const permissionPanels = [...document.querySelectorAll('[data-how-to-permission]')];

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
