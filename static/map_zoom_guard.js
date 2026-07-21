(() => {
    'use strict';

    // Zoom 5 still permits broad regional browsing, but prevents a whole-world
    // viewport where metre-radius Spot circles collapse and map bounds can exceed
    // the server's valid longitude range.
    const MIN_SPOT_MAP_ZOOM = 5;

    if (!window.L?.Map?.mergeOptions) return;
    window.L.Map.mergeOptions({ minZoom: MIN_SPOT_MAP_ZOOM });
})();
