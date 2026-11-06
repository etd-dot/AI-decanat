#include "DecanatFaceRequest.h"

#if DECANAT_WITH_ACE
#include "ACEAudioCurveSourceComponent.h"
#include "Async/Async.h"

FDecanatFaceRequest::FDecanatFaceRequest(UACEAudioCurveSourceComponent* InConsumer)
    : Consumer(InConsumer) {}

void FDecanatFaceRequest::Enqueue(FDelivery&& Delivery)
{
    if (IsCancelled()) return;
    Pending.Enqueue(MoveTemp(Delivery));
    ScheduleDrain();
}

void FDecanatFaceRequest::ScheduleDrain()
{
    if (!bDrainScheduled.exchange(true)) {
        AsyncTask(ENamedThreads::GameThread, [Self = AsShared()] { Self->Drain(); });
    }
}

void FDecanatFaceRequest::Drain()
{
    check(IsInGameThread());
    FDelivery Delivery;
    while (Pending.Dequeue(Delivery)) {
        if (!IsCancelled()) Delivery(Consumer.Get());
    }
    bDrainScheduled.store(false);
    if (!Pending.IsEmpty()) ScheduleDrain();
}

void FDecanatFaceRequest::PrepareNewStream_AnyThread(int32 StreamID, uint32 SampleRate,
    int32 NumChannels, int32 SampleByteSize)
{
    Enqueue([=](UACEAudioCurveSourceComponent* Target) {
        // Running on the game thread makes SDK setup synchronous. The following
        // queued chunks can then be consumed without waiting for a task here.
        Target->PrepareNewStream_AnyThread(StreamID, SampleRate, NumChannels, SampleByteSize);
    });
}

void FDecanatFaceRequest::ConsumeAnimData_AnyThread(const FACEAnimDataChunk& Chunk, int32 StreamID)
{
    if (IsCancelled()) return;
    // The SDK's views expire when this callback returns; own every queued byte.
    TArray<FName> Names(Chunk.BlendShapeNames);
    TArray<float> Weights(Chunk.BlendShapeWeights);
    TArray<uint8> Audio(Chunk.AudioBuffer);
    const double Timestamp = Chunk.Timestamp;
    const EACEAnimDataStatus Status = Chunk.Status;
    Enqueue([Names = MoveTemp(Names), Weights = MoveTemp(Weights), Audio = MoveTemp(Audio),
             Timestamp, Status, StreamID](UACEAudioCurveSourceComponent* Target) {
        FACEAnimDataChunk Owned{};
        Owned.BlendShapeNames = Names;
        Owned.BlendShapeWeights = Weights;
        Owned.AudioBuffer = Audio;
        Owned.Timestamp = Timestamp;
        Owned.Status = Status;
        Target->ConsumeAnimData_AnyThread(Owned, StreamID);
    });
}
#endif
