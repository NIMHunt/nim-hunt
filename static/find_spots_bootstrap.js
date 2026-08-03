import { installFindSpotsSearchTransport } from './find_spots_search_transport.js?v=wrapped-search-v1-20260803';

installFindSpotsSearchTransport();

void import('./find_spots.js?v=wrapped-search-v1-20260803').catch((error) => {
    console.error('NimHunt could not start the Find Spots page.', error);
});
