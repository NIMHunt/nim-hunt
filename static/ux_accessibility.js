import { installCreateSpotProgressiveSettings } from './create_spot_progressive.js?v=ux-accessibility-v1-20260817';
import { installFindSpotsDemo } from './find_spots_demo.js?v=demo-visual-polish-v1-20260817';
import { installFindSpotsCreateCtaGuard } from './find_spots_user_cta.js?v=desktop-create-cta-v1-20260817';

installCreateSpotProgressiveSettings();
const findSpotsRuntime = installFindSpotsDemo();
installFindSpotsCreateCtaGuard(findSpotsRuntime);
