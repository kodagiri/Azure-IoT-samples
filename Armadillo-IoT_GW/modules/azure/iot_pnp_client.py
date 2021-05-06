import asyncio
import json

from azure.iot.device import Message, MethodResponse
from azure.iot.device.aio import IoTHubDeviceClient
from azure.iot.device.aio import ProvisioningDeviceClient
from azure.iot.device.exceptions import CredentialError

from modules.azure.model_config_base import ModelConfigBase
from modules.azure.model_dev_base import ModelDevBase


class IoTPnPClient:
    def __init__(self, modelConfig, modelDev):
        self._modelConfig  = modelConfig
        self._modelDev     = modelDev
        self._clientHandle = None
        self._isConnected  = False

    def is_connected(self):
        return self._isConnected

    async def auth_and_connect(self):
        model_id  = self._modelDev.model_id()
        auth_conf = self._modelConfig.auth_props()

        provisioning_device_client = ProvisioningDeviceClient.create_from_symmetric_key(
            provisioning_host=auth_conf[ModelConfigBase.IOTHUB_DEVICE_DPS_ENDPOINT],
            registration_id=auth_conf[ModelConfigBase.IOTHUB_DEVICE_DPS_DEVICE_ID],
            id_scope=auth_conf[ModelConfigBase.IOTHUB_DEVICE_DPS_ID_SCOPE],
            symmetric_key=auth_conf[ModelConfigBase.IOTHUB_DEVICE_DPS_DEVICE_KEY]
        )
        provisioning_device_client.provisioning_payload = {
            "modelId": model_id
        }
        registration_result = await provisioning_device_client.register()
        if registration_result.status != "assigned":
            print("Could not provision device.")
            return False
        else:
            print("Device was assigned")

        registration_state = registration_result.registration_state
        print(registration_state.assigned_hub)
        print(registration_state.device_id)
        device_client = IoTHubDeviceClient.create_from_symmetric_key(
            symmetric_key=auth_conf[ModelConfigBase.IOTHUB_DEVICE_DPS_DEVICE_KEY],
            hostname=registration_state.assigned_hub,
            device_id=registration_state.device_id,
            product_info=model_id
        )
        await device_client.connect()
        await device_client.patch_twin_reported_properties(self._modelDev.props())
        device_client.on_method_request_received = self._method_request_handler
        device_client.on_twin_desired_properties_patch_received = self._twin_patch_handler
        self._clientHandle = device_client
        self._isConnected  = True

        return True

    async def disconnect(self):
        if not self._isConnected:
            await self._clientHandle.disconnect()
            self._isConnected = False

    async def shutdown(self):
        await self.disconnect()
        await self._clientHandle.shutdown()

    async def send_telemetry(self, telemetry_data):
        if not self._isConnected:
            return False
        msg = Message(json.dumps(telemetry_data))
        msg.content_enconding = "utf-8"
        msg.content_type      = "application/json"
        print("Send message")
        try:
            await self._clientHandle.send_message(msg)
        except CredentialError:
            print("connection has broken.")
            self._isConnected = False
            return False

        return True

    async def _method_request_handler(self, method_request):
        post_proc = None
        (result, post_proc) = await self._modelDev.execute_commnad(
            method_request.name, method_request.payload)
        status = 400
        if not result:
            print("Could not execute the direct method: ", method_request.name)
            payload = {"result": False, "data": "unknown method"}
        else:
            payload = {
                "result": True,
                "data": (method_request.name + " is succeeded" if isinstance(result, bool) else result)
            }
            status = 200
        method_response = MethodResponse.create_from_method_request(
            method_request, status, payload
        )

        await self._clientHandle.send_method_response(method_response)
        if post_proc:
            post_proc()

    async def _twin_patch_handler(self, patch):
        ignore_keys = ["__t", "$version"]
        version = patch["$version"]
        props   = {}
        for name, value in patch.items():
            if not name in ignore_keys:
                new_value = self._modelDev.set_prop(name, value)
                props[name] = {
                    "ac": 200,
                    "ad": "Successfully executed patch",
                    "av": version,
                    "value": new_value
                }
        await self._clientHandle.patch_twin_reported_properties(props)

#
# End of File
#
