import { installCreateSpotProgressiveSettings } from './create_spot_progressive.js?v=ux-accessibility-v1-20260817';
import { installFindSpotsDemo } from './find_spots_demo.js?v=demo-visual-polish-v1-20260817';
import { installFindSpotsCreateCtaGuard } from './find_spots_user_cta.js?v=demo-onboarding-v1-20260818';
import { installFindSpotsCreateModal } from './find_spots_create.js?v=demo-onboarding-v1-20260818';

installCreateSpotProgressiveSettings();
const findSpotsRuntime = installFindSpotsDemo();
installFindSpotsCreateCtaGuard(findSpotsRuntime);
installFindSpotsCreateModal(findSpotsRuntime);
