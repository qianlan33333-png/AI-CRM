(function () {
  "use strict";

  document.addEventListener("DOMContentLoaded", () => {
    const CustomerProfile = window.CustomerProfile || {};
    const root = CustomerProfile.root && CustomerProfile.root();
    if (!root) return;

    if (CustomerProfile.bootBasicSections) {
      CustomerProfile.bootBasicSections(root);
    }
    if (CustomerProfile.bootCustomerPulse) {
      CustomerProfile.bootCustomerPulse(root);
    }
    if (CustomerProfile.bootFollowupOrchestrator) {
      CustomerProfile.bootFollowupOrchestrator(root);
    }
    if (CustomerProfile.bootAutomation) {
      CustomerProfile.bootAutomation(root);
    }
    if (CustomerProfile.scrollToInitialSection) {
      CustomerProfile.scrollToInitialSection(root);
    }
  });
})();
