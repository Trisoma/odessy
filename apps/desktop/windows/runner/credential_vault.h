#ifndef RUNNER_CREDENTIAL_VAULT_H_
#define RUNNER_CREDENTIAL_VAULT_H_

#include <flutter/binary_messenger.h>
#include <flutter/encodable_value.h>
#include <flutter/method_channel.h>
#include <memory>

std::unique_ptr<flutter::MethodChannel<flutter::EncodableValue>>
CreateCredentialVault(flutter::BinaryMessenger* messenger);

#endif
