import { installCreateSpotProgressiveSettings } from './create_spot_progressive.js?v=create-form-layout-v2-20260818';
import { installFindSpotsDemo } from './find_spots_demo.js?v=demo-distance-250m-v1-20260818';
import { installFindSpotsCreateCtaGuard } from './find_spots_user_cta.js?v=empty-state-loop-hotfix-v1-20260818';
import { installFindSpotsCreateModal } from './find_spots_create.js?v=demo-onboarding-v1-20260818';

installCreateSpotProgressiveSettings();
const findSpotsRuntime = installFindSpotsDemo();
installFindSpotsCreateCtaGuard(findSpotsRuntime);
installFindSpotsCreateModal(findSpotsRuntime);
