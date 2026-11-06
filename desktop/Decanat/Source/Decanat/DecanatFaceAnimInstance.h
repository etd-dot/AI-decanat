#pragma once

#include "CoreMinimal.h"
#include "Animation/AnimInstance.h"
#include "DecanatFaceAnimInstance.generated.h"

// The face graph reads this before ARKit mapping. ACE can supply its own
// non-verbal performance while speaking; idle blinking does not need inference.
UCLASS(Transient, Blueprintable)
class DECANAT_API UDecanatFaceAnimInstance : public UAnimInstance
{
    GENERATED_BODY()
public:
    virtual void NativeInitializeAnimation() override;
    virtual void NativeUpdateAnimation(float DeltaSeconds) override;
    UPROPERTY(Transient, BlueprintReadOnly, Category="Decanat|Face") float IdleBlink = 0.0f;
    UPROPERTY(Transient, BlueprintReadOnly, Category="Decanat|Face") TObjectPtr<USkeletalMeshComponent> BodySource;
    float GetClock() const { return Clock; }
private:
    float Clock = 0.0f;
    float NextBlink = 3.2f;
    float BlinkStarted = -1.0f;
};
