// Centralised human-facing interface text for NimHunt pages.
// Keep labels, notice text, and reusable UI wording here rather than scattering them across page scripts.

const DEFAULT_APP_NAME = 'NimHunt';
const DEFAULT_NIMIQ_PAY_URL = 'https://nimpay.app';

export const COMMON_TEXT = {
    notice: {
        readMore: 'Read more',
        ok: 'OK',
    },
};

export const REPORT_REASON_OPTIONS = [
    { value: 10, label: 'Spam or repeated content' },
    { value: 20, label: 'Inappropriate or offensive content' },
    { value: 30, label: 'False or misleading location' },
    { value: 40, label: 'Scam or unsafe reward' },
    { value: 90, label: 'Other' },
];

export const SPOT_TEXT = {
    fallbackTitle: 'NimHunt Spot',
    unknownArea: 'Unknown area',
    noDescription: 'No description provided.',
    copySpotLink: 'Copy spot link',
    passwordRequiredTooltip: 'This spot requires a password.',
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
        active: 'Active',
        upcoming: 'Upcoming',
        ended: 'Ended',
        completed: 'Completed',
        cancelled: 'Cancelled',
        banned: 'Banned',
        unknown: 'Unknown',
    },
};

export function makeSpotDetailText({ appName = DEFAULT_APP_NAME, nimiqPayUrl = DEFAULT_NIMIQ_PAY_URL } = {}) {
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
            spotName: (title) => title || SPOT_TEXT.fallbackTitle,
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


export function makeMySpotsText({ appName = DEFAULT_APP_NAME, nimiqPayUrl = DEFAULT_NIMIQ_PAY_URL } = {}) {
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
            missing: 'No Deposit',
        },
        ownerActions: {
            edit: 'Edit',
            deposit: 'Deposit',
            publish: 'Publish',
            cancel: 'Cancel Spot',
            publishStartTimePastTooltip: 'Change the start time before publishing.',
            publishUnavailableTooltip: 'This draft cannot be published yet.',
        },
        deposit: {
            title: 'Deposit NIM',
            confirm: 'Confirm',
            confirming: 'Confirming…',
            cancel: 'Cancel',
            confirmBody: ({ title, amountText }) => `Deposit ${amountText} for '${title}'?`,
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
            confirmBody: ({ title, refundText, feeText, remainingLost = false, noRemaining = false }) => {
                if (noRemaining) {
                    return `Are you sure you want to cancel '${title}'? There are no remaining funds to return.`;
                }
                if (remainingLost) {
                    return `Are you sure you want to cancel '${title}'? Remaining funds will be lost.`;
                }
                return `Are you sure you want to cancel '${title}'? Remaining funds will be returned, minus the cancellation fee. Estimated refund: ${refundText}. Cancellation fee: ${feeText}.`;
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
            claimDuration: (duration) => `Requires a claim duration of ${duration}`,
            claimRadius: (radius) => `Claim radius: ${radius} m`,
            claimsPerUser: (maxClaimsPerUser) => (maxClaimsPerUser <= 0 ? 'Unlimited claims per user' : `${maxClaimsPerUser} claims per user`),
            claimCodes: ({ unused, total }) => `Claim codes: ${unused} unused / ${total} total`,
            claimCodesOnPublish: ({ total }) => `Claim codes will be created when published (${total} total)`,
            reports: ({ pending, total }) => `Reports: ${pending} pending / ${total} total`,
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

export function makeCreateSpotFormText({ appName = DEFAULT_APP_NAME, nimiqPayUrl = DEFAULT_NIMIQ_PAY_URL } = {}) {
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
            startsInPast: 'Starts must be in the future.',
            standardParticipantsRequired: 'Standard spots require a finite Total Participants value.',
            prizeCountInvalid: 'Prize Count cannot exceed Total Participants unless Total Participants is Unlimited.',
            prizedrawPasswordInvalid: 'Prizedraws do not use passwords.',
            perClaim: (amountText) => `(${amountText} per claim)`,
            perPrize: (amountText) => `(${amountText} per prize)`,
            passwordRequiresFiniteParticipants: 'Use Password requires a finite Total Participants value.',
        },
    };
}
