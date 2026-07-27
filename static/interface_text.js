import {
    applyTextCatalogue,
    getPreferredLanguage,
    resolveTextCatalogue,
} from './localisation.js';

// Centralised human-facing interface text for NimHunt pages.
// Keep labels, notice text, and reusable UI wording here rather than scattering them across page scripts.

const DEFAULT_APP_NAME = 'NimHunt';
const DEFAULT_NIMIQ_PAY_URL = 'https://nimpay.app';

const COMMON_TEXT_EN = {
    notice: {
        readMore: 'Read more',
        ok: 'OK',
    },
    actions: {
        copy: 'Copy',
        copied: 'Copied',
        shareOnX: 'Share on X',
    },
};

const REPORT_REASON_OPTIONS_EN = [
    { value: 10, label: 'Spam or repeated content' },
    { value: 20, label: 'Inappropriate or offensive content' },
    { value: 30, label: 'False or misleading location' },
    { value: 40, label: 'Scam or unsafe reward' },
    { value: 90, label: 'Other' },
];

const SPOT_TEXT_EN = {
    fallbackTitle: 'NimHunt Spot',
    unknownArea: 'Unknown area',
    noDescription: 'No description provided.',
    copySpotLink: 'Copy spot link',
    passwordRequiredTooltip: 'This spot requires a password.',
    durationRequiredTooltip: 'This spot requires you to remain within its area for a set duration.',
    specialUserTooltip: 'This is a special user',
    ownerClaimCodes: {
        title: (count) => `Claim Codes (${count})`,
        loading: 'Loading claim codes…',
        unused: 'Unused',
        copy: 'Copy claim code',
        copied: 'Copied',
        loadFailed: 'Claim codes could not be loaded.',
    },
    type: {
        spot: 'Spot',
        prizeDraw: 'Prizedraw',
    },
    status: {
        draft: 'Draft',
        depositing: 'Depositing',
        processing: 'Processing',
        deposited: 'Deposited',
        active: 'Active',
        upcoming: 'Upcoming',
        ended: 'Ended',
        completed: 'Complete',
        cancelled: 'Cancelled',
        cancelling: 'Cancelling',
        banned: 'Banned',
        unknown: 'Unknown',
    },
};

function buildSpotDetailTextEnglish({ appName = DEFAULT_APP_NAME, nimiqPayUrl = DEFAULT_NIMIQ_PAY_URL } = {}) {
    return {
        nimiqPay: {
            deviceIdReason: `Submit a ${appName} spot report from this device.`,
        },
        claim: {
            actions: {
                claim: 'Claim',
                enter: 'Enter',
                begin: 'Begin',
                unavailable: 'Unavailable',
            },
            title: 'Claim Spot',
            confirm: ({ action }) => action || 'claim',
            confirming: 'Confirming…',
            cancel: 'Cancel',
            reward: (amountText) => `Reward: ${amountText}`,
            prizeValue: (amountText) => `Prize value: ${amountText}`,
            durationRequired: (duration) => `You must remain within the area for ${duration}.`,
            passwordRequired: 'A password is required.',
            codeUsedWhenVerificationStarts: 'This one-time code is used when verification begins and is not restored if the duration check later fails.',
            passwordLabel: 'Password',
            passwordPlaceholder: 'Enter password',
            captchaLabel: 'Captcha',
            captchaQuestion: ({ a, b }) => `What is ${a} + ${b}?`,
            captchaPlaceholder: 'Answer',
            participants: ({ current, max }) => `Participants: ${current}${max > 0 ? ` / ${max}` : ''}`,
            prizes: ({ count }) => `${count} ${count === 1 ? 'prize' : 'prizes'} available`,
            unavailableTooltip: 'This spot cannot be claimed right now.',
            passwordIncomplete: 'Enter the password and complete the captcha.',
            failed: {
                title: 'Could not claim Spot',
                body: 'The claim could not be created. Check your location and try again.',
            },
        },
        report: {
            open: 'Report ⚑',
            title: 'Make a Report',
            spotName: (title) => title || SPOT_TEXT_EN.fallbackTitle,
            reasonLabel: 'Reason',
            reasonPlaceholder: 'Select a reason',
            detailsLabel: 'Details',
            detailsPlaceholder: 'Optional details',
            detailsLimit: (remaining) => `${remaining} ${remaining === 1 ? 'character' : 'characters'} remaining`,
            captchaLabel: 'Captcha',
            captchaQuestion: ({ a, b }) => `What is ${a} + ${b}?`,
            captchaPlaceholder: 'Answer',
            confirm: 'Confirm',
            confirming: 'Submitting…',
            cancel: 'Cancel',
            incomplete: 'Choose a reason and complete the captcha.',
            noDeviceTooltip: `Open ${appName} inside Nimiq Pay to identify this device.`,
            submitted: {
                title: 'Report Submitted',
                body: 'Thank you. Your report has been submitted.',
            },
            walletUnavailable: {
                title: `Open ${appName} in Nimiq Pay`,
                body: `${appName} needs Nimiq Pay to identify this device before submitting a report.`,
                href: nimiqPayUrl,
                linkText: 'Open Nimiq Pay',
            },
            failed: {
                title: 'Could not submit report',
                body: 'The report could not be submitted. Check the form and try again.',
            },
            alreadyReported: {
                title: 'Already Reported',
                body: 'You have already reported this spot.',
            },
        },
        ownerClaimCodes: {
            title: (count) => `Claim Codes (${count})`,
            loading: 'Loading claim codes…',
            unused: 'Unused',
            copy: 'Copy claim code',
            copied: 'Copied',
            loadFailed: 'Claim codes could not be loaded.',
        },
    };
}


function buildMySpotsTextEnglish({ appName = DEFAULT_APP_NAME, nimiqPayUrl = DEFAULT_NIMIQ_PAY_URL } = {}) {
    return {
        nimiqPay: {
            deviceIdReason: `View the ${appName} spots created by this device.`,
        },
        notices: {
            walletUnavailable: {
                title: `Open ${appName} in Nimiq Pay`,
                body: `${appName} needs Nimiq Pay to identify this device. My Spots is locked until this app is opened inside Nimiq Pay.`,
                href: nimiqPayUrl,
                linkText: 'Open Nimiq Pay',
            },
            testUserMissing: {
                title: 'Test user missing',
                body: 'Desktop test mode is enabled, but the mock test user does not exist. Run spoof.py, then reload this page.',
            },
            banned: {
                title: 'Account unavailable',
                body: `This device account can no longer use ${appName}.`,
            },
            loadFailed: {
                title: 'Could not load My Spots',
                body: 'The spots created by this device could not be loaded. Return home and try again.',
            },
            mapSetupFailed: {
                title: 'Map setup failed',
                body: 'The spot map could not be loaded. The list below should still work.',
            },
        },
        status: {
            emptyBeforeLink: 'You have not created any spots yet. Try ',
            emptyLink: 'making one',
            emptyAfterLink: '.',
            userFallback: (id) => `User ${id}`,
        },
        draftDeposit: {
            ready: 'Ready',
            partial: (amountText) => `Partial Deposit - ${amountText}`,
            processingFee: 'Fee Processing',
            missing: 'No Deposit',
        },
        ownerActions: {
            edit: 'Edit',
            deposit: 'Deposit',
            publish: 'Publish',
            cancel: 'Cancel Spot',
            cancelDraft: 'Cancel Draft',
            publishStartTimePastTooltip: 'The configured end time has already elapsed.',
            publishUnavailableTooltip: 'This draft cannot be published yet.',
        },
        deposit: {
            title: 'Deposit NIM',
            confirm: 'Confirm',
            confirming: 'Confirming…',
            cancel: 'Cancel',
            confirmLead: ({ title }) => `Deposit NIM for ${title}?`,
            spotFundingLine: (amountText) => `Spot Funds: ${amountText}`,
            creationFeeLine: (amountText) => `Creation Fee: ${amountText}`,
            depositNowLine: (amountText) => `Total Deposit: ${amountText}`,
            intentFailed: {
                title: 'Could not prepare deposit',
                body: 'The deposit request could not be prepared. Refresh My Spots and try again.',
            },
            failed: {
                title: 'Deposit not recorded',
                body: 'The Nimiq Pay deposit request did not complete, or the transaction could not be recorded.',
            },
        },
        publish: {
            title: 'Publish Spot',
            confirm: 'Confirm',
            publishing: 'Publishing…',
            cancel: 'Cancel',
            confirmBody: ({ title }) => `Publish '${title}'? It will become visible to other users, and this draft can no longer be edited.`,
            failed: {
                title: 'Could not publish Spot',
                body: 'This draft could not be published yet. Check that it is complete and fully deposited.',
            },
        },
        cancelSpot: {
            title: 'Cancel Spot',
            confirm: 'Confirm',
            confirming: 'Cancelling…',
            cancel: 'Cancel',
            manualReviewNotice: 'Failed deposit records will remain attached to this Spot for manual review and are not included in the estimated refund.',
            confirmBody: ({ title, remainingLost = false, noRemaining = false }) => {
                if (noRemaining) {
                    return `Are you sure you want to cancel '${title}'? There are no remaining funds to return.`;
                }
                if (remainingLost) {
                    return `Are you sure you want to cancel '${title}'? Remaining funds will be lost.`;
                }
                return `Are you sure you want to cancel '${title}'?`;
            },
            failed: {
                title: 'Could not cancel Spot',
                body: 'This spot could not be cancelled. Refresh My Spots and try again.',
            },
        },
        createSpot: {
            title: 'Create a Spot',
            openButton: 'Create Spot',
            submit: 'Create',
            submitting: 'Creating…',
            cancel: 'Cancel',
            titlePlaceholder: 'Spot Title',
            standard: 'Standard',
            prizeDraw: 'Prizedraw',
            standardTooltip: 'Every user receives a reward',
            prizeDrawTooltip: 'Randomly-selected users receive a reward',
            invalidTitle: ({ min, max }) => `Spot title must be between ${min} and ${max} characters.`,
            captchaLabel: 'Captcha',
            captchaQuestion: ({ a, b }) => `What is ${a} + ${b}?`,
            captchaPlaceholder: 'Answer',
            captchaIncomplete: 'Complete the captcha before creating a spot.',
            draftLimitReached: ({ limit }) => `You have reached the draft limit (${limit}). Publish or delete a draft before creating another.`,
            draftLimitTooltip: ({ limit }) => `You have reached the draft limit (${limit}).`,
            walletUnavailable: {
                title: `Open ${appName} in Nimiq Pay`,
                body: `${appName} needs Nimiq Pay to create a spot.`,
                href: nimiqPayUrl,
                linkText: 'Open Nimiq Pay',
            },
            createFailed: {
                title: 'Could not create Spot',
                body: 'The draft spot could not be created. Check the title and try again.',
            },
        },
        spotDetail: {
            totalValue: (amountText) => `${amountText} total value`,
            readyToPublish: 'Ready to publish',
            progress: ({ used, max, word }) => `${used} / ${max} ${word} used`,
            prizeDraw: ({ prizeCount }) => `Prizedraw with ${prizeCount} ${prizeCount === 1 ? 'prize' : 'prizes'}`,
            prizedrawValue: ({ prizeCount, amountText }) => `Prizedraw with ${prizeCount} ${prizeCount === 1 ? 'prize' : 'prizes'} (${amountText} each)`,
            ran: ({ starts, ends }) => `Ran ${starts} until ${ends}`,
            scheduled: ({ starts, ends }) => `Scheduled ${starts} until ${ends}`,
            activeWindow: ({ starts, ends }) => `Active ${starts} until ${ends}`,
            claimDuration: (duration) => `Must remain on Spot for ${duration}`,
            claimRadius: (radius) => `Claim radius: ${radius} m`,
            claimsPerUser: (maxClaimsPerUser) => (maxClaimsPerUser <= 0 ? 'Unlimited claims per user' : `${maxClaimsPerUser} claims per user`),
            claimCodes: ({ unused, total }) => `Claim codes: ${unused} unused / ${total} total`,
            claimCodesOnPublish: ({ total }) => `Claim codes will be created when published (${total} total)`,
            reports: ({ pending, total }) => `Reports: ${pending} pending / ${total} total`,
            refundTransaction: ({ amountText, destination, status, shortHash }) => `Refund: ${amountText} sent to ${destination} (${status}${shortHash ? `, tx ${shortHash}` : ''})`,
        },
        sections: {
            active: {
                title: 'Active Spots',
                empty: 'No active spots right now.',
            },
            upcoming: {
                title: 'Upcoming Spots',
                empty: 'No upcoming published spots.',
            },
            draft: {
                title: 'Draft Spots',
                empty: 'No draft spots right now.',
            },
            previous: {
                title: 'Previous Spots',
                empty: 'No previous spots yet.',
            },
        },
    };
}

function buildCreateSpotFormTextEnglish({ appName = DEFAULT_APP_NAME, nimiqPayUrl = DEFAULT_NIMIQ_PAY_URL } = {}) {
    return {
        nimiqPay: {
            deviceIdReason: `Edit this ${appName} draft spot.`,
        },
        notices: {
            walletUnavailable: {
                title: `Open ${appName} in Nimiq Pay`,
                body: `${appName} needs Nimiq Pay to confirm that this device created the draft spot.`,
                href: nimiqPayUrl,
                linkText: 'Open Nimiq Pay',
            },
            notOwner: {
                title: 'Spot unavailable',
                body: 'This draft spot was not created by this device.',
            },
            loadFailed: {
                title: 'Could not load draft spot',
                body: 'The draft spot could not be loaded. Return to My Spots and try again.',
            },
            saveFailed: {
                title: 'Could not save draft',
                body: 'The draft spot could not be saved. Check the form and try again.',
            },
            saved: {
                title: 'Draft saved',
                body: 'Your draft spot has been updated.',
            },
            deleteConfirm: {
                title: 'Delete Draft',
                body: (title) => `Are you sure you want to delete '${title}'?`,
                confirm: 'Delete',
                cancel: 'Cancel',
            },
            deleteFailed: {
                title: 'Could not delete draft',
                body: 'This draft spot could not be deleted. Try again.',
            },
        },
        form: {
            untitled: 'Untitled Spot',
            checking: 'Checking this draft spot.',
            titleInvalid: ({ min, max }) => `Title must be between ${min} and ${max} characters.`,
            coordinates: ({ lat, long }) => `${lat.toFixed(5)}, ${long.toFixed(5)}`,
            selectedLocation: 'Selected location',
            locationNotSet: 'Location not set',
            saving: 'Saving…',
            save: 'Save Draft',
            changesAlreadySaved: 'changes are already saved',
            locationRequired: 'Choose a location on the map.',
            totalNimMinimum: (minimum) => `Total NIM must be at least ${minimum}.`,
            payoutTooLow: ({ minimum, kind }) => `Per ${kind} payout must be at least ${minimum} NIM.`,
            payoutTooLowTooltip: ({ minimum, kind }) => `Per ${kind} payout is too low. Minimum: ${minimum} NIM.`,
            startsInvalid: 'Starts must be a valid date and time.',
            startsInPast: 'Starts may be in the past if the Spot has not ended.',
            endsInPast: 'The configured end time has already elapsed.',
            standardParticipantsRequired: 'Standard spots require a finite Total Participants value.',
            prizedrawParticipantsMinimum: (minimum) => `Prizedraws require at least ${minimum} participants unless Total Participants is Unlimited.`,
            prizedrawLimitsInvalid: 'For a finite Prizedraw, Claims Per User and Prize Count must both be less than Total Participants.',
            prizedrawPasswordInvalid: 'Prizedraws do not use passwords.',
            perClaim: (amountText) => `(${amountText} per claim)`,
            perPrize: (amountText) => `(${amountText} per prize)`,
            passwordRequiresFiniteParticipants: 'Use Password requires a finite Total Participants value.',
        },
    };
}


// Translation overrides are intentionally kept separate from the English source.
// Add future language codes here (for example, `de: { ... }`) and provide only
// the keys that differ. Missing keys automatically inherit English.
export const INTERFACE_TRANSLATIONS = {
    en: {},
};

function sectionCatalogues(section, englishText) {
    const catalogues = { en: englishText };
    for (const [language, overrides] of Object.entries(INTERFACE_TRANSLATIONS)) {
        if (language === 'en' || !overrides?.[section]) continue;
        catalogues[language] = overrides[section];
    }
    return catalogues;
}

function localiseSection(section, englishText, language = getPreferredLanguage()) {
    return resolveTextCatalogue(sectionCatalogues(section, englishText), language).text;
}

// English aliases preserve the old imports while new code can use the getters
// below to receive the active language catalogue.
export const COMMON_TEXT = COMMON_TEXT_EN;
export const REPORT_REASON_OPTIONS = REPORT_REASON_OPTIONS_EN;
export const SPOT_TEXT = SPOT_TEXT_EN;

export function getCommonText({ language = getPreferredLanguage() } = {}) {
    return localiseSection('common', COMMON_TEXT_EN, language);
}

export function getReportReasonOptions({ language = getPreferredLanguage() } = {}) {
    return localiseSection('reportReasons', REPORT_REASON_OPTIONS_EN, language);
}

export function getSpotText({ language = getPreferredLanguage() } = {}) {
    return localiseSection('spot', SPOT_TEXT_EN, language);
}

export function makeSpotDetailText({
    appName = DEFAULT_APP_NAME,
    nimiqPayUrl = DEFAULT_NIMIQ_PAY_URL,
    language = getPreferredLanguage(),
} = {}) {
    return localiseSection(
        'spotDetail',
        buildSpotDetailTextEnglish({ appName, nimiqPayUrl }),
        language,
    );
}

export function makeMySpotsText({
    appName = DEFAULT_APP_NAME,
    nimiqPayUrl = DEFAULT_NIMIQ_PAY_URL,
    language = getPreferredLanguage(),
} = {}) {
    return localiseSection(
        'mySpots',
        buildMySpotsTextEnglish({ appName, nimiqPayUrl }),
        language,
    );
}

export function makeCreateSpotFormText({
    appName = DEFAULT_APP_NAME,
    nimiqPayUrl = DEFAULT_NIMIQ_PAY_URL,
    language = getPreferredLanguage(),
} = {}) {
    return localiseSection(
        'createSpotForm',
        buildCreateSpotFormTextEnglish({ appName, nimiqPayUrl }),
        language,
    );
}

export function makeHomeText({
    appName = DEFAULT_APP_NAME,
    displayNameMin = 3,
    displayNameMax = 18,
    language = getPreferredLanguage(),
} = {}) {
    const english = {
        nimiqPay: {
            deviceIdReason: `Create or find your ${appName} device account.`,
        },
        notices: {
            walletUnavailable: {
                title: `Open ${appName} in Nimiq Pay`,
                body: `${appName} needs Nimiq Pay to identify this device. My Spots and My Claims are locked until this app is opened inside Nimiq Pay.`,
            },
            testUserMissing: {
                title: 'Test user missing',
                body: 'Desktop test mode is enabled, but the mock test user does not exist. Run spoof.py, then reload this page.',
            },
            banned: {
                title: 'Account unavailable',
                body: `This device account can no longer use ${appName}.`,
            },
            setupFailed: {
                title: 'Home setup failed',
                body: `${appName} could not initialise the home page. Reload the mini app or open it again from Nimiq Pay.`,
            },
            firstVisit: {
                title: `Welcome to ${appName}`,
                body: `Your ${appName} device account has been created. You can now find spots, create spots, and track your claims from this device.`,
                buttonText: "Let's Go!",
            },
        },
        metrics: {
            activeSpots: (n) => `${n.toLocaleString()} Active ${n === 1 ? 'Spot' : 'Spots'}`,
            dailyUsers: (n) => `${n.toLocaleString()} Daily ${n === 1 ? 'User' : 'Users'}`,
        },
        profile: {
            editLabel: 'Edit display name',
            inputLabel: 'Display name',
            save: 'Save',
            saving: 'Saving…',
            cancel: 'Cancel',
            invalidLength: () => `Display name must be between ${displayNameMin} and ${displayNameMax} characters.`,
            saveFailed: 'Display name could not be saved. Try again.',
            invalidResponse: 'The server did not understand the display-name update.',
        },
        status: {
            checkingPay: 'Checking Nimiq Pay…',
            connectedPay: 'Connected through Nimiq Pay.',
            notConnectedPay: 'Not connected through Nimiq Pay.',
            testUser: 'Using desktop test user.',
            guestWelcome: `Open ${appName} inside Nimiq Pay to identify this device.`,
            guestBeforePay: `Open ${appName} inside `,
            guestAfterPay: ' to identify this device.',
            userWelcome: (displayName) => `Welcome, ${displayName}`,
            userFallback: (id) => `User ${id}`,
        },
        locked: {
            walletRequired: 'This feature requires Nimiq Pay.',
            accountUnavailable: `This account cannot use ${appName}.`,
            userRequired: `Open ${appName} in Nimiq Pay first.`,
            locationRequired: 'Find Spots requires location access.',
        },
        debug: {
            available: 'available',
            notAvailable: 'not available',
            locationNotRequested: 'not requested on Home',
            unknown: 'unknown',
            userNotLoaded: 'not loaded',
            userLoaded: (user) => `${user.display_name || `User ${user.id}`}(#${user.id})`,
        },
    };
    return localiseSection('home', english, language);
}

export function makeFindSpotsText({
    appName = DEFAULT_APP_NAME,
    language = getPreferredLanguage(),
} = {}) {
    const english = {
        notices: {
            locationUnavailable: {
                title: 'Location unavailable',
                body: `${appName} could not read your location. You can still move the map manually. Distances are hidden until location is available.`,
            },
            mapSetupFailed: {
                title: 'Map setup failed',
                body: 'The spot map could not be loaded. Reload the page and try again.',
            },
            spotLoadFailed: {
                title: 'Could not load spots',
                body: 'The visible spot list could not be refreshed. Move the map or reload the page.',
            },
        },
        status: {
            listTitle: 'Spots in Your Area',
            listTitleWithCount: (n) => `${n} ${n === 1 ? 'Spot' : 'Spots'} in Your Area`,
            emptyBeforeLink: 'No spots meet your criteria. Be the first to ',
            emptyLink: 'make one',
            emptyAfterLink: '.',
            ctaBeforeLink: "Not found what you're looking for? Try ",
            ctaLink: 'making one',
            ctaAfterLink: '.',
        },
        cancelSpot: {
            title: 'Cancel Spot',
            confirm: 'Confirm',
            confirming: 'Cancelling…',
            cancel: 'Cancel',
            body: ({ title, refund, fee }) => `Cancel '${title}'? Estimated refund: ${refund}. Cancellation fee: ${fee}.`,
            failed: {
                title: 'Could not cancel Spot',
                body: 'The Spot could not be cancelled.',
            },
        },
    };
    return localiseSection('findSpots', english, language);
}

export function makeMyClaimsText({
    appName = DEFAULT_APP_NAME,
    nimiqPayUrl = DEFAULT_NIMIQ_PAY_URL,
    language = getPreferredLanguage(),
} = {}) {
    const english = {
        nimiqPay: {
            deviceIdReason: `View the ${appName} claims made by this device.`,
        },
        requestFailed: 'Request failed.',
        status: {
            success: 'Success',
            failed: 'Failed',
            pending: 'Pending',
            unknown: 'Unknown',
        },
        recent: 'recently',
        statusLabel: 'Status: ',
        claimed: ({ when, value }) => `Claimed ${when} (${value})`,
        participants: (count) => `${count} current participants`,
        emptyBeforeLink: 'You have no claims. ',
        emptyLink: 'Go collect',
        emptyAfterLink: ' some!',
        title: (count) => `My Claims (${count})`,
        fallbackTitle: 'Claim',
        mapSetupFailed: {
            title: 'Map setup failed',
            body: 'The claim map could not be loaded. Reload the page and try again.',
        },
        walletUnavailable: {
            title: `Open ${appName} in Nimiq Pay`,
            body: `${appName} needs Nimiq Pay to identify this device before showing My Claims.`,
            href: nimiqPayUrl,
            linkText: 'Open Nimiq Pay',
        },
        loadFailed: 'My Claims could not be loaded.',
    };
    return localiseSection('myClaims', english, language);
}

export function makeClaimDetailText({
    appName = DEFAULT_APP_NAME,
    nimiqPayUrl = DEFAULT_NIMIQ_PAY_URL,
    language = getPreferredLanguage(),
} = {}) {
    const english = {
        nimiqPay: {
            deviceIdReason: `View this ${appName} claim from this device.`,
        },
        requestFailed: 'Request failed.',
        geolocationUnavailable: 'Geolocation is not available.',
        status: {
            success: 'Success',
            failed: 'Failed',
            pending: 'Pending',
        },
        mapAriaLabel: 'Claim spot map',
        now: 'now',
        statusLabel: 'Status: ',
        claimed: ({ when, value }) => `Claimed ${when} (${value})`,
        participants: (count) => `${count} current participants`,
        locationScore: (score) => `Location score ${score}%`,
        fallbackTitle: 'Claim',
        unknownArea: 'Unknown area',
        locationNeeded: {
            title: 'Location needed',
            body: `Keep ${appName} open and allow location access until this claim finishes. If no fresh location reaches the server for too long, the claim will fail.`,
        },
        walletUnavailable: {
            title: `Open ${appName} in Nimiq Pay`,
            body: `${appName} needs Nimiq Pay to identify this device before showing the claim.`,
            href: nimiqPayUrl,
            linkText: 'Open Nimiq Pay',
        },
        loadFailed: 'This claim could not be loaded.',
    };
    return localiseSection('claimDetail', english, language);
}

const STATIC_INTERFACE_TEXT_EN = {
    common: {
        noticeBody: 'Something needs your attention.',
        readMore: 'Read more',
        ok: 'OK',
        backHome: 'Back home',
        home: 'Home',
        cancel: 'Cancel',
        confirm: 'Confirm',
        delete: 'Delete',
    },
    home: {
        returnHome: 'Return to Home',
        checkingPay: 'Checking Nimiq Pay…',
        preparing: 'Preparing your home screen.',
        save: 'Save',
        findSpots: 'Find Spots',
        findSpotsDetail: 'Search the map for nearby current and upcoming spots.',
        mySpots: 'My Spots',
        mySpotsDetail: 'Review spots you created.',
        myClaims: 'My Claims',
        myClaimsDetail: 'See claims you have made.',
        mainNavigation: 'Main navigation',
        troubleshooting: 'Troubleshooting',
        troubleshootingAria: 'Troubleshooting information',
        nimiqPay: 'Nimiq Pay',
        location: 'Location',
        language: 'Language',
        user: 'User',
        checking: 'checking',
        locationNotRequested: 'not requested on Home',
        metricsAria: 'NimHunt metrics',
        activeSpots: '0 Active Spots',
        dailyUsers: '0 Daily Users',
        errorNotFound: 'This page could not be found.',
    },
    claim: {
        title: 'Claim',
        detailsAria: 'Claim details',
        loading: 'Loading claim.',
    },
    createSpot: {
        backAria: 'Back to My Spots',
        back: 'My Spots',
        title: 'Create Spot',
        checkingOwnerAria: 'Checking spot owner',
        checking: 'Checking this draft spot.',
        formAria: 'Complete spot creation form',
        deleteTitle: 'Delete Draft',
        deleteBody: 'Are you sure you want to delete this draft?',
        titleLabel: 'Title',
        location: 'Location',
        notSet: 'Not set',
        mapAria: 'Choose spot location',
        radius: 'Radius',
        description: 'Description',
        stayDuration: 'Stay Duration',
        stayDurationTooltip: "How long a user must remain inside this spot's radius before claiming.",
        stayDurationAria: 'Explain Stay Duration',
        claimsPerUser: 'Claims Per User',
        claimsPerUserTooltip: 'How many times an individual user can claim a reward from this spot.',
        claimsPerUserAria: 'Explain Claims Per User',
        totalParticipants: 'Total Participants',
        prizeCount: 'Prize Count',
        totalNim: 'Total NIM',
        starts: 'Starts',
        endsAfter: 'Ends After',
        usePassword: 'Use Password',
        usePasswordTooltip: 'Require a claim password. NimHunt will create one claim code for each participant.',
        usePasswordAria: 'Explain Use Password',
        requireClaimCodes: 'Require claim codes',
        saveDraft: 'Save Draft',
    },
    findSpots: {
        reportTitle: 'Make a Report',
        reason: 'Reason',
        reasonPlaceholder: 'Select a reason',
        details: 'Details',
        captcha: 'Captcha',
        claimTitle: 'Claim Spot',
        password: 'Password',
        answerPlaceholder: 'Answer',
        title: 'Find Spots',
        mapAria: 'Spot map',
        listAria: 'Visible spots',
        visibleSpots: 'Visible Spots',
        filtersAria: 'Spot filters',
        active: 'Active',
        upcoming: 'Upcoming',
        includePrizedraws: 'Incl. Prizedraws',
        testLocation: 'Test Location',
        testLocationTitle: 'Use the centre of the map as your temporary test location.',
    },
    mySpots: {
        title: 'My Spots',
        mapAria: 'Your spot map',
        checking: 'Checking your spots.',
        createSpot: 'Create Spot',
    },
    myClaims: {
        title: 'My Claims',
        mapAria: 'Claim map',
        checking: 'Checking your claims.',
    },
    spot: {
        title: 'Spot',
        detailsAria: 'Spot details',
        loading: 'Loading spot.',
    },
};

export function applyStaticInterfaceText(
    root = globalThis.document,
    { language = getPreferredLanguage() } = {},
) {
    return applyTextCatalogue(root, sectionCatalogues('static', STATIC_INTERFACE_TEXT_EN), language);
}
