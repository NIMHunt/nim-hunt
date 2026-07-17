// Pure Prizedraw limit helpers shared by the Create Spot form and its tests.
// A value of 0 retains NimHunt's existing meaning of Unlimited.

function finiteSortedOptions(options) {
    return [...new Set((options || [])
        .map(Number)
        .filter((value) => Number.isFinite(value) && value > 0))]
        .sort((a, b) => a - b);
}

export function smallestOptionAbove(options, value) {
    return finiteSortedOptions(options).find((option) => option > Number(value)) ?? null;
}

export function largestOptionBelow(options, value) {
    const candidates = finiteSortedOptions(options).filter((option) => option < Number(value));
    return candidates.length ? candidates[candidates.length - 1] : null;
}

export function prizedrawLimitsAreValid({
    maxClaimsPerUser,
    maxTotalClaims,
    prizeCount,
    minimumFiniteParticipants = 2,
}) {
    const perUser = Number(maxClaimsPerUser || 0);
    const total = Number(maxTotalClaims || 0);
    const prizes = Number(prizeCount || 0);

    if (total === 0) return prizes > 0;
    if (!Number.isFinite(total) || total < Number(minimumFiniteParticipants)) return false;
    if (!Number.isFinite(prizes) || prizes <= 0 || prizes >= total) return false;
    if (perUser > 0 && perUser >= total) return false;
    return true;
}

export function adjustedPrizedrawLimits({
    changedName,
    maxClaimsPerUser,
    maxTotalClaims,
    prizeCount,
    participantOptions,
    perUserOptions,
    prizeOptions,
}) {
    let perUser = Number(maxClaimsPerUser || 0);
    let total = Number(maxTotalClaims || 0);
    let prizes = Number(prizeCount || 0);

    // Unlimited total participants does not constrain finite per-user or prize
    // values. Unlimited per-user retains its existing special meaning too.
    if (total <= 0) {
        return { maxClaimsPerUser: perUser, maxTotalClaims: total, prizeCount: prizes };
    }

    if (perUser > 0 && perUser >= total) {
        if (changedName === 'perUser') {
            total = smallestOptionAbove(participantOptions, perUser)
                ?? total;
        } else {
            perUser = largestOptionBelow(perUserOptions, total)
                ?? perUser;
        }
    }

    if (prizes > 0 && prizes >= total) {
        if (changedName === 'prizeCount') {
            total = smallestOptionAbove(participantOptions, prizes)
                ?? total;
        } else {
            prizes = largestOptionBelow(prizeOptions, total)
                ?? prizes;
        }
    }

    // Raising Total Participants for one relationship can expose the other, so
    // make one final conservative pass using the nearest existing lower option.
    if (perUser > 0 && perUser >= total) {
        perUser = largestOptionBelow(perUserOptions, total) ?? perUser;
    }
    if (prizes > 0 && prizes >= total) {
        prizes = largestOptionBelow(prizeOptions, total) ?? prizes;
    }

    return { maxClaimsPerUser: perUser, maxTotalClaims: total, prizeCount: prizes };
}
