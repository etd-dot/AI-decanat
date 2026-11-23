#include "DecanatMotion.h"

#include <cassert>

int main()
{
    using Pose = FDecanatMotion::EPose;
    FDecanatMotion Motion;
    assert(Motion.GetPose() == Pose::Seated);

    const auto Stand = Motion.Begin(true);
    assert(Motion.IsMoving() && !Motion.IsStanding());
    assert(Motion.Complete(Stand));
    assert(Motion.IsStanding() && !Motion.IsMoving());
    assert(!Motion.Complete(Stand)); // Completion is acknowledged only once.

    const auto Sit = Motion.Begin(false);
    assert(Motion.GetPose() == Pose::Sitting);
    assert(!Motion.Complete(Stand)); // A late stand cannot cancel the current sit.
    assert(Motion.GetPose() == Pose::Sitting);
    assert(Motion.Complete(Sit));
    assert(Motion.GetPose() == Pose::Seated);

    const auto AbortedStand = Motion.Begin(true);
    const auto ReplacementSit = Motion.Begin(false);
    assert(!Motion.Complete(AbortedStand));
    assert(Motion.GetPose() == Pose::Sitting);
    assert(Motion.Complete(ReplacementSit));

    const auto Disconnected = Motion.Begin(true);
    Motion.Reset();
    const auto NextStudent = Motion.Begin(true);
    assert(!Motion.Complete(Disconnected)); // A previous session cannot finish this transition.
    assert(Motion.GetPose() == Pose::Rising);
    assert(Motion.Complete(NextStudent));

    const auto InterruptedSit = Motion.Begin(false);
    const auto RestartedStand = Motion.Begin(true);
    assert(!Motion.Complete(InterruptedSit));
    assert(Motion.Complete(RestartedStand));
    assert(Motion.IsStanding());

    Motion.Reset();
    assert(!Motion.Complete(RestartedStand));
    assert(Motion.GetPose() == Pose::Seated && !Motion.IsMoving());
}
