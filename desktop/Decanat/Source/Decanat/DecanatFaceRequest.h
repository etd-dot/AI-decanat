#pragma once

#include "CoreMinimal.h"

#if DECANAT_WITH_ACE
#include "AnimDataConsumer.h"
#include "Containers/Queue.h"
#include "UObject/StrongObjectPtr.h"
#include <atomic>

class UACEAudioCurveSourceComponent;

// One registry identity per utterance. ACE callbacks must never wait for the
// game thread: cancellation can otherwise deadlock while holding its registry.
class FDecanatFaceRequest final : public IACEAnimDataConsumer,
    public TSharedFromThis<FDecanatFaceRequest, ESPMode::ThreadSafe>
{
public:
    explicit FDecanatFaceRequest(UACEAudioCurveSourceComponent* InConsumer);
    void Cancel() { bCancelled.store(true); }
    bool IsCancelled() const { return bCancelled.load(); }
    virtual void PrepareNewStream_AnyThread(int32 StreamID, uint32 SampleRate,
        int32 NumChannels, int32 SampleByteSize) override;
    virtual void ConsumeAnimData_AnyThread(const FACEAnimDataChunk& Chunk, int32 StreamID) override;

private:
    using FDelivery = TFunction<void(UACEAudioCurveSourceComponent*)>;
    TStrongObjectPtr<UACEAudioCurveSourceComponent> Consumer;
    TQueue<FDelivery, EQueueMode::Mpsc> Pending;
    std::atomic<bool> bCancelled{false};
    std::atomic<bool> bDrainScheduled{false};
    void Enqueue(FDelivery&& Delivery);
    void ScheduleDrain();
    void Drain();
};
#endif
