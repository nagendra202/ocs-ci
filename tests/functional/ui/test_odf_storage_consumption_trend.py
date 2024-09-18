import logging
import math
import re
import time

from ocs_ci.framework import config
from ocs_ci.framework.testlib import (
    ManageTest,
    tier2,
    ui,
    ignore_leftovers,
    black_squad,
)
from ocs_ci.helpers import helpers
from ocs_ci.helpers.osd_resize import basic_resize_osd
from ocs_ci.ocs import constants
from ocs_ci.ocs.cluster import get_used_and_total_capacity_in_gibibytes
from ocs_ci.ocs.ocp import OCP
from ocs_ci.ocs.resources.pod import (
    get_age_of_cluster_in_days,
    get_mgr_pods,
    get_ceph_tools_pod,
    delete_pods,
    get_prometheus_pods,
)
from ocs_ci.ocs.resources.storage_cluster import get_storage_size
from ocs_ci.ocs.ui.validation_ui import ValidationUI

logger = logging.getLogger(__name__)


POD_OBJ = OCP(kind=constants.POD, namespace=config.ENV_DATA["cluster_namespace"])


@tier2
@ui
@ignore_leftovers
@black_squad
class TestConsumptionTrendUI(ManageTest):
    def get_est_days_from_ui(self):
        """
        Get the value of 'Estimated days until full' from the UI

        Returns:
            est_days: (int)

        """
        validation_ui_obj = ValidationUI()
        collected_list_of_days_and_avg = (
            validation_ui_obj.odf_storagesystems_consumption_trend()
        )
        est_days = re.search(r"\d+", collected_list_of_days_and_avg[0]).group()
        logger.info(f"'Estimated days until full' from the UI : {est_days}")
        return int(est_days)

    def get_avg_consumption_from_ui(self):
        """
        Get the value of 'Average storage consumption' from the UI

        Returns:
            average: (float)

        """
        validation_ui_obj = ValidationUI()
        collected_list_of_days_and_avg = (
            validation_ui_obj.odf_storagesystems_consumption_trend()
        )
        average = float(
            re.search(r"-?\d+\.*\d*", collected_list_of_days_and_avg[1]).group()
        )
        logger.info(f"'Average of storage consumption per day' from the UI : {average}")
        return average

    def calculate_est_days_manually(self):
        """
        Calculates the 'Estimated days until full' manually by:
        1. Get the age of the cluster in days
        2. Get used capacity of the cluster
        3. Get total capacity of the cluster
        4. Calculate average consumption of the storage per day
        5. Calculate the 'Estimated days until full' by using average and available capacity.

        Returns:
            estimated_days_calculated: (int)

        """
        number_of_days = get_age_of_cluster_in_days()
        logger.info(f"Age of the cluster in days: {number_of_days}")
        list_of_used_and_total_capacity = get_used_and_total_capacity_in_gibibytes()
        used_capacity = list_of_used_and_total_capacity[0]
        logger.info(f"The used capacity from the cluster is: {used_capacity}")
        total_capacity = list_of_used_and_total_capacity[1]
        available_capacity = total_capacity - used_capacity
        logger.info(f"The available capacity from the cluster is: {available_capacity}")
        average = used_capacity / number_of_days
        logger.info(f"Average of storage consumption per day: {average}")
        estimated_days_calculated = available_capacity / average
        logger.info(f"Estimated days calculated are {estimated_days_calculated}")
        return math.floor(estimated_days_calculated)

    def test_consumption_trend_card_ui(self, setup_ui_class):
        """
        Verify the widget for “Consumption trend”
            1. Login to the ODF dashboard and check the widget for “Consumption trend” is displayed or not.
            2. Verify the text information on the widget
        """
        validation_ui_obj = ValidationUI()
        collected_list_of_days_and_avg = (
            validation_ui_obj.odf_storagesystems_consumption_trend()
        )
        avg_txt = "Average storage consumption"
        est_days_txt = "Estimated days until full"
        logger.info(
            f"Estimated days text information from the wizard is: {collected_list_of_days_and_avg[0]}"
        )
        logger.info(
            f"Average information from the wizard is: {collected_list_of_days_and_avg[1]}"
        )
        assert (
            est_days_txt in collected_list_of_days_and_avg[0]
        ), f"Text information for Estimated days is wrong in {collected_list_of_days_and_avg[0]}"
        assert (
            avg_txt in collected_list_of_days_and_avg[1]
        ), f"Text information for Average is wrong in {collected_list_of_days_and_avg[1]}"

    def test_estimated_days_until_full_ui(self, setup_ui_class):
        """
        Verify the accuracy of ‘Estimated days until full’  in the widget
            1. Get the value of storage utilised, total storage, age of the cluster
                and calculate Average of storage consumption and 'Estimated days until full'.
            2. Compare the above calculated value with the ‘Estimated days until full’ in the widget (UI)

        """
        est_days = self.get_est_days_from_ui()
        average = self.get_avg_consumption_from_ui()
        logger.info(f"From the UI, Estimated Days: {est_days} and Average: {average}")
        estimated_days_calculated = self.calculate_est_days_manually()
        first_validation = est_days == estimated_days_calculated
        # rel_tol 0.1 means upto 10% tolerance, which means that difference between  manual and UI est days is up to 10%
        second_validation = math.isclose(
            est_days, estimated_days_calculated, rel_tol=0.1
        )
        if first_validation:
            logger.info(
                "Manually calculated and UI values are exactly matched for 'Estimated days until full'"
            )
        elif second_validation:
            logger.warning(
                "Manually calculated and UI values are matched with upto 10% difference for 'Estimated days until full'"
            )
        assert (
            first_validation or second_validation
        ), "'Estimated days until full' is wrongly displayed in UI"

    def test_consumption_trend_with_mgr_failover(self, setup_ui_class):
        """
        Verify storage consumption trend with Mgr failover
            1. Failover active mgr pod, the other mgr will become active now.
            2. Test storage consumption trend from UI is accurate after mgr failover.

        """
        logger.info("Get mgr pods objs")
        mgr_objs = get_mgr_pods()
        toolbox = get_ceph_tools_pod()
        active_mgr_pod_output = toolbox.exec_cmd_on_pod("ceph mgr stat")
        active_mgr_pod_suffix = active_mgr_pod_output.get("active_name")
        logger.info(f"The active MGR pod is {active_mgr_pod_suffix}")
        active_mgr_deployment_name = "rook-ceph-mgr-" + active_mgr_pod_suffix
        for obj in mgr_objs:
            if active_mgr_pod_suffix in obj.name:
                active_mgr_pod = obj.name

        logger.info(f"Scale down {active_mgr_deployment_name} to 0")
        helpers.modify_deployment_replica_count(
            deployment_name=active_mgr_deployment_name, replica_count=0
        )
        POD_OBJ.wait_for_delete(resource_name=active_mgr_pod)
        # Below sleep is mandatory for mgr failover, if not the same pod will become active again.
        time.sleep(60)
        logger.info(f"Scale down {active_mgr_deployment_name} to 1")
        helpers.modify_deployment_replica_count(
            deployment_name=active_mgr_deployment_name, replica_count=1
        )
        assert (
            len(get_mgr_pods()) == 2
        ), "one of the mgr pod is still down after scale down and up"
        logger.info("Mgr failovered successfully")

        logger.info("Now the testing will begin for consumption trend UI")
        est_days = self.get_est_days_from_ui()
        average = self.get_avg_consumption_from_ui()
        logger.info(f"From the UI, Estimated Days: {est_days} and Average: {average}")
        estimated_days_calculated = self.calculate_est_days_manually()
        first_validation = est_days == estimated_days_calculated
        # rel_tol 0.1 means upto 10% tolerance, which means that difference between  manual and UI est days is up to 10%
        second_validation = math.isclose(
            est_days, estimated_days_calculated, rel_tol=0.1
        )
        if first_validation:
            logger.info(
                "Manually calculated and UI values are exactly matched for 'Estimated days until full'"
            )
        elif second_validation:
            logger.warning(
                "Manually calculated and UI values are matched with upto 10% difference for 'Estimated days until full'"
            )
        assert (
            first_validation or second_validation
        ), "'Estimated days until full' is wrongly displayed in UI"

    def test_consumption_trend_with_prometheus_failures(self, setup_ui_class):
        """
        Fail prometheus and verify the Consumption trend in the ODF dashboard to make sure
        ‘Estimated days until full’ and 'Average' reflects accurate value.

            1. Fail the prometheus pods by deleting them. New pods will be created automatically.
            2. When the new prometheus pods  up, Consumption trend should be displayed in the dashboard.
            3. Check the values for  ‘Estimated days until full’ and ‘Average of storage consumption per day’
            4. Should show close to the values before deleting the prometheus pod

        """
        logger.info("Get the value of 'Estimated days until full' from UI")
        est_days_before = self.get_est_days_from_ui()
        logger.info(
            f"The value of 'Estimated days until full' from UI is {est_days_before} before failing prometheus"
        )
        average_before = self.get_avg_consumption_from_ui()
        logger.info(
            f"'Average of storage consumption per day' from UI is {average_before} before failing prometheus"
        )
        logger.info("Bring down the prometheus")
        list_of_prometheus_pod_obj = get_prometheus_pods()
        delete_pods(list_of_prometheus_pod_obj)
        time.sleep(120)
        est_days_after = self.get_est_days_from_ui()
        logger.info(
            f"From the UI, Estimated Days: {est_days_after} after prometheus recovered from failure"
        )
        average_after = self.get_avg_consumption_from_ui()
        logger.info(
            f"'Average of storage consumption' from UI is {average_after} after prometheus recovered from failure"
        )
        # rel_tol 0.1 means upto 10% tolerance which means that difference between  manual and UI est days is up to 10%
        assert math.isclose(
            est_days_before, est_days_after, rel_tol=0.1
        ), "Estimated days until full did not match before and after prometheus fail"
        assert math.isclose(
            average_before, average_after, rel_tol=0.1
        ), "'Average of storage consumption per day' did not match before and after prometheus fail"

    def test_consumption_trend_after_osd_resize(self, setup_ui_class):
        """
        Verify consumption trend after OSD resize
        1. Get the value of 'Estimated days until full' value from the UI.
        2. Perform OSD resize.
        3. Verify the new size is reflecting in the consumption trend dashboard or not.
        4. 'Estimated days until full' value in the UI, should increase after OSD resize.

        """
        logger.info("Get the value of 'Estimated days until full' from UI")
        est_days_before = self.get_est_days_from_ui()
        logger.info("Performing OSD resize")
        basic_resize_osd(get_storage_size())
        logger.info("After OSD resize, checking consumption trend UI")
        logger.info(
            "Get the value of 'Estimated days until full' from UI after OSD resize"
        )
        est_days_after = self.get_est_days_from_ui()
        assert (
            est_days_after > est_days_before
        ), f"'Estimated days until full' {est_days_after} did not increase after OSD resize"
